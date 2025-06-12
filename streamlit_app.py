import os
import queue
import threading
import time

import streamlit as st

from downloader import authenticate_google_api, extract_file_id_from_drive_link, download_meet_recording_to_temp_file, \
    list_meet_recordings
from processor import process_video_file
from settings import DEFAULT_SETTINGS, SETTING_DESCRIPTIONS


def run_streamlit_app():
    st.set_page_config(layout="wide")
    st.title("Google Meet Recording Processor")

    # Initialize session state variables
    if 'output_messages' not in st.session_state:
        st.session_state.output_messages = []
    if 'google_credentials' not in st.session_state:
        st.session_state.google_credentials = None
    if 'auth_attempted' not in st.session_state:
        st.session_state.auth_attempted = False
    if 'is_running' not in st.session_state:
        st.session_state.is_running = False
    if 'is_success' not in st.session_state:
        st.session_state.is_success = False
    if 'message_queue' not in st.session_state:
        st.session_state.message_queue = queue.Queue()  # Thread-safe queue for messages

    # Placeholder for the progress bar and its related variables
    if 'progress_bar_widget' not in st.session_state:
        st.session_state.progress_bar_widget = None
    if 'progress_bar_label' not in st.session_state:
        st.session_state.progress_bar_label = ""
    if 'progress' not in st.session_state:
        st.session_state.progress = 0

    # Helper to append messages to session state (from main thread)
    def append_message_to_session_state(message_type, message_content):
        st.session_state.output_messages.append((message_type, message_content))

    # Sidebar for Google API Authentication
    st.sidebar.header("Google API Authentication")

    if st.session_state.google_credentials is None and not st.session_state.auth_attempted:
        st.sidebar.warning("Google API authentication required for Meet operations.")
        if st.sidebar.button("Authenticate with Google", disabled=st.session_state.is_running):
            st.session_state.auth_attempted = True
            st.session_state.output_messages = []
            append_message_to_session_state("info",
                                            "Authenticating... Check your browser for a new tab and complete the authorization.")

            with st.spinner("Authenticating..."):
                try:
                    # Run the async authentication function in a separate thread
                    st.session_state.google_credentials = authenticate_google_api()
                    if st.session_state.google_credentials:
                        append_message_to_session_state("success", "Authentication successful!")
                    else:
                        append_message_to_session_state("error", "Authentication failed. Check console for details.")
                except Exception as e:
                    append_message_to_session_state("error", f"Authentication error: {e}. Check console for details.")
            st.rerun()
    elif st.session_state.google_credentials is None and st.session_state.auth_attempted:
        st.sidebar.error("Authentication failed or not completed. Please try again.")
    else:
        st.sidebar.success("Authenticated with Google API.")
        if st.sidebar.button("Clear Authentication", disabled=st.session_state.is_running):
            if os.path.exists('token.json'):
                os.remove('token.json')
                st.session_state.google_credentials = None
                st.session_state.auth_attempted = False
                st.session_state.output_messages = []
                append_message_to_session_state("info",
                                                "Authentication token cleared. Please re-authenticate if needed.")
                st.rerun()

    st.sidebar.markdown("---")

    # Sidebar for general settings
    st.sidebar.header("Application Settings")
    current_settings = {}
    for key, default_value in DEFAULT_SETTINGS.items():
        description = SETTING_DESCRIPTIONS.get(key, f"Default: {default_value}")

        if isinstance(default_value, bool):
            current_settings[key] = st.sidebar.checkbox(
                f"{key.replace('_', ' ').title()}",
                value=default_value,
                help=description
            )
        elif isinstance(default_value, int):
            current_settings[key] = st.sidebar.number_input(
                f"{key.replace('_', ' ').title()}",
                value=default_value,
                step=1,
                help=description
            )
        elif isinstance(default_value, float):
            current_settings[key] = st.sidebar.number_input(
                f"{key.replace('_', ' ').title()}",
                value=float(default_value),
                help=description
            )
        elif isinstance(default_value, str):
            if len(default_value) > 50 or '\n' in default_value:
                current_settings[key] = st.sidebar.text_area(
                    f"{key.replace('_', ' ').title()}",
                    value=str(default_value),
                    help=description
                )
            else:
                current_settings[key] = st.sidebar.text_input(
                    f"{key.replace('_', ' ').title()}",
                    value=str(default_value),
                    help=description
                )
        else:
            current_settings[key] = st.sidebar.text_input(
                f"{key.replace('_', ' ').title()}",
                value=str(default_value),
                help=description
            )

    # Main content area
    st.header("Operation Mode")
    mode = st.radio(
        "Choose an operation mode:",
        ("Process Local MP4 File", "Process Google Meet Recording (Link)", "List Recent Recordings"),
        key="mode_selection"
    )

    input_value = ""
    if mode == "Process Local MP4 File":
        input_value = st.text_input("Enter local MP4 file path:", key="local_path_input")
    elif mode == "Process Google Meet Recording (Link)":
        input_value = st.text_input("Enter Google Drive link to Meet recording:", key="drive_link_input")
        if not st.session_state.google_credentials:
            st.warning("Please authenticate with Google to use this mode.")
    elif mode == "List Recent Recordings":
        st.info("This mode will list recent Google Meet recordings and their Drive IDs.")
        if not st.session_state.google_credentials:
            st.warning("Please authenticate with Google to use this mode.")

    st.markdown("---")

    # Process button
    execute_button_clicked = st.button("Execute Operation", disabled=st.session_state.is_running)

    # Output display area - this container will be dynamically updated
    # It must be defined OUTSIDE the 'if execute_button_clicked block'
    # so that it exists on every rerun.
    output_display_container = st.container()

    if execute_button_clicked:
        st.session_state.is_running = True  # Set flag to True
        st.session_state.output_messages = []  # Clear previous output
        output_display_container.empty()

        # Explicitly create placeholder for the progress bar
        st.session_state.progress = 0
        st.session_state.progress_bar_label = ""
        st.session_state.progress_bar_widget = st.progress(0)

        # Start a new thread for the blocking operation
        def target_function_in_thread(mode, input_value, current_settings, credentials, message_queue):
            """
            This function runs in a separate thread and performs the main logic.
            It receives all necessary Streamlit state data via arguments,
            and communicates back via the message_queue.
            """
            try:
                if mode == "Process Local MP4 File":
                    if input_value:
                        if os.path.exists(input_value) and os.path.isfile(input_value) and input_value.lower().endswith(
                                '.mp4'):
                            process_video_file(input_value, current_settings, message_queue)
                        else:
                            message_queue.put(("error",
                                               f"Invalid local MP4 file path: `{input_value}`. Please ensure it exists and is an MP4 file."))
                    else:
                        message_queue.put(("error", "Please enter a local MP4 file path."))

                elif mode == "Process Google Meet Recording (Link)":
                    if not credentials:
                        message_queue.put(
                            ("error", "Google API authentication is required for processing from Google Meet."))
                    elif input_value:
                        file_id = extract_file_id_from_drive_link(input_value)
                        if file_id:
                            download_meet_recording_to_temp_file(credentials, file_id, process_video_file,
                                                                 current_settings, message_queue)
                        else:
                            message_queue.put(("error",
                                               "Could not extract a valid Google Drive File ID from the provided link. Please check the link format."))
                    else:
                        message_queue.put(("error", "Please enter a Google Drive link."))

                elif mode == "List Recent Recordings":
                    if not credentials:
                        message_queue.put(
                            ("error", "Google API authentication is required for listing Google Meet recordings."))
                    else:
                        recordings = list_meet_recordings(credentials, message_queue)
                        if recordings:
                            message_queue.put(("info", "Available Meet Recordings (in 'FILE_GENERATED' state):"))
                            for idx, rec in enumerate(recordings):
                                message_queue.put(("text",
                                                   f"  **[{idx}]** Conference: `{rec['conference_record_id'].split('/')[-1]}`, "
                                                   f"Recording: `{rec['recording_id'].split('/')[-1]}`, "
                                                   f"Drive File ID: `{rec['file_id']}`"))
                        else:
                            message_queue.put(("info",
                                               "No Google Meet recordings found that are in 'FILE_GENERATED' state with a valid Drive File ID via the API."))
            except Exception as ex:
                message_queue.put(("error", f"An unexpected error occurred in background task: {ex}"))
            finally:
                message_queue.put(("DONE", None))  # Signal end of task

        # Pass necessary current state values as arguments to the new thread's target function
        thread = threading.Thread(
            target=target_function_in_thread,
            args=(mode, input_value, current_settings, st.session_state.google_credentials, st.session_state.message_queue)
        )
        thread.start()

    # Loop to continuously update output display while operation is running
    while st.session_state.is_running:
        # Check for new messages from the queue
        try:
            # Process all available messages in the queue in this rerun
            messages_received = 0
            while messages_received < 10:
                msg_type, msg_content = st.session_state.message_queue.get_nowait()
                messages_received += 1
                if msg_type == "DONE":
                    st.session_state.is_running = False
                    st.session_state.is_success = True
                    break  # Exit loop
                elif msg_type == "progress_init":
                    st.session_state.progress_bar_label = msg_content
                elif msg_type == "progress_update":
                    st.session_state.progress = msg_content
                elif msg_type == "progress_complete":
                    st.session_state.progress = 0
                    st.session_state.progress_bar_label = ""
                else:
                    st.session_state.output_messages.append((msg_type, msg_content))
        except queue.Empty:
            pass  # No new messages yet

        # Display all accumulated messages
        with output_display_container:
            for msg_type, msg_content in st.session_state.output_messages:
                if msg_type == "info":
                    st.info(msg_content)
                elif msg_type == "text":
                    st.text(msg_content)
                elif msg_type == "error":
                    st.error(msg_content)
                elif msg_type == "warning":
                    st.warning(msg_content)
                elif msg_type == "success":
                    st.success(msg_content)
                else:
                    st.write(msg_content)
            if st.session_state.progress_bar_widget:
                st.session_state.progress_bar_widget.progress(st.session_state.progress, st.session_state.progress_bar_label)

        # Crucial for Streamlit to re-render and update the UI
        # A small sleep allows the app to yield control and re-run.
        time.sleep(0.1)
        st.rerun()  # Force a rerun to display updates

    # Final display after operation is complete (only if not already rerunning from inside the loop)
    if not st.session_state.is_running:
        with output_display_container:  # Ensure final messages are rendered
            output_display_container.empty()  # Clear placeholder first
            for msg_type, msg_content in st.session_state.output_messages:
                if msg_type == "info":
                    st.info(msg_content)
                elif msg_type == "text":
                    st.text(msg_content)
                elif msg_type == "error":
                    st.error(msg_content)
                elif msg_type == "warning":
                    st.warning(msg_content)
                elif msg_type == "success":
                    st.success(msg_content)
                else:
                    st.write(msg_content)

    if st.session_state.is_success:
        st.balloons()
        st.session_state.is_success = False
