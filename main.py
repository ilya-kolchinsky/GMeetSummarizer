import argparse
import os
import sys

from downloader import authenticate_google_api, extract_file_id_from_drive_link, download_meet_recording_to_temp_file, \
    list_meet_recordings
from processor import process_video_file
from settings import DEFAULT_SETTINGS, SETTING_DESCRIPTIONS
from streamlit_app import run_streamlit_app


def main():
    parser = argparse.ArgumentParser(
        description="Process Google Meet recordings or local MP4 files.",
        formatter_class=argparse.RawTextHelpFormatter  # Preserve newlines in help text
    )

    # An argument to launch the Streamlit UI
    parser.add_argument(
        '--ui',
        action='store_true',
        help="Launch the Streamlit graphical user interface. When this flag is used, no other arguments should be specified."
    )

    # Mutually exclusive group for input sources
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        '-l', '--local-file',
        type=str,
        help="Path to a local MP4 file with a Google Meet recording."
    )
    group.add_argument(
        '-g', '--google-drive-link',
        type=str,
        help="Google Drive link to a Google Meet recording.\n"
             "(E.g., https://drive.google.com/file/d/SOME_FILE_ID/view)"
             "Requires Google API authentication."
    )
    group.add_argument(
        '-L', '--list-recordings',
        action='store_true',
        help="List recent Google Meet recordings and their Drive File IDs.\n"
             "Requires Google API authentication."
    )

    # Dynamically add arguments for each setting from DEFAULT_SETTINGS
    for key, default_value in DEFAULT_SETTINGS.items():
        arg_name = f"--{key.replace('_', '-')}"  # Convert snake_case to kebab-case for CLI

        # Get description from SETTING_DESCRIPTIONS, with a fallback
        help_text = SETTING_DESCRIPTIONS.get(key, f"Override setting for '{key}'. Default: {default_value}")

        # Determine argument type for argparse
        arg_type = type(default_value)
        if isinstance(default_value, bool):
            # For booleans, use store_true/store_false actions for flags
            # If default is False, --arg-name makes it True.
            # If default is True, --arg-name makes it False (implies --no-arg-name).
            # This is standard argparse boolean flag behavior.
            parser.add_argument(
                arg_name,
                action='store_true' if default_value is False else 'store_false',
                help=f"{help_text} (Flag to set to {not default_value})"
            )
        else:
            parser.add_argument(
                arg_name,
                type=arg_type,
                default=argparse.SUPPRESS,  # Use argparse.SUPPRESS to indicate argument not explicitly set
                help=f"{help_text} (Default: {default_value})"
            )

    args = parser.parse_args()

    if args.ui and len(sys.argv) > 2:
        parser.error("The '--ui' argument cannot be combined with any other command-line arguments. Please specify '--ui' alone.")

    if args.ui:
        run_streamlit_app()
        return

    # --- Start: Settings Combination Logic ---
    final_settings = DEFAULT_SETTINGS.copy()

    # 1. Apply environment variables (uppercase key names)
    for key in final_settings:
        env_var_name = key.upper()
        if env_var_name in os.environ:
            env_value = os.environ[env_var_name]
            try:
                # Type conversion based on the default value's type
                if isinstance(final_settings[key], bool):
                    converted_value = env_value.lower() in ('true', '1', 't', 'y', 'yes')
                elif isinstance(final_settings[key], int):
                    converted_value = int(env_value)
                elif isinstance(final_settings[key], float):
                    converted_value = float(env_value)
                else:
                    converted_value = env_value  # Keep as string or original type
                final_settings[key] = converted_value
                print(f"[Settings] '{key}' overridden by environment variable '{env_var_name}' to '{converted_value}'.")
            except ValueError:
                print(f"[WARNING] Could not convert environment variable '{env_var_name}' value '{env_value}' "
                      f"to expected type ({type(final_settings[key]).__name__}) for setting '{key}'. Using current value.")

    # 2. Apply command-line arguments (highest precedence)
    for key in DEFAULT_SETTINGS:
        arg_attr_name = key  # Argparse attributes match dest by default

        # For boolean flags, check if the argument was explicitly provided
        if isinstance(DEFAULT_SETTINGS[key], bool):
            # Check if the value in args is different from the default_value,
            # indicating it was set by the command line (e.g., --process-audio to toggle)
            if getattr(args, arg_attr_name) != DEFAULT_SETTINGS[key]:
                final_settings[key] = getattr(args, arg_attr_name)
                print(f"[Settings] '{key}' overridden by command-line argument to '{final_settings[key]}'.")
        # For other types, check if it's not the 'SUPPRESS' sentinel
        elif hasattr(args, arg_attr_name) and getattr(args, arg_attr_name) is not argparse.SUPPRESS:
            final_settings[key] = getattr(args, arg_attr_name)
            print(f"[Settings] '{key}' overridden by command-line argument to '{final_settings[key]}'.")

    print("\nEffective configuration for this run:")
    for key, value in final_settings.items():
        print(f"  {key}: {value}")
    # --- End: Settings Combination Logic ---

    input_sources_specified = sum([
        1 if args.local_file else 0,
        1 if args.google_drive_link else 0,
        1 if args.list_recordings else 0
    ])

    if input_sources_specified > 1:
        parser.error(
            "Only one input source (--local-file, --google-drive-link, or --list-recordings) can be specified.")

    credentials = None
    # Attempt authentication only if a Google-related operation is requested
    if args.google_drive_link or args.list_recordings:
        if os.path.exists('credentials.json'):
            credentials = authenticate_google_api()
            if credentials:
                print("Google API Authentication successful!")
            else:
                print("Google API Authentication failed. Google Meet operations will not be possible.")
                print("Please ensure 'credentials.json' is correctly configured and located.")
        else:
            print("No 'credentials.json' found. Google Meet operations will not be possible.")
            print("Please ensure 'credentials.json' is present for Google Meet functionality.")
            exit(1)  # Exit if Google operation requested but no credentials

    if args.local_file:
        print(f"[Main] Processing local file: {args.local_file}")
        process_video_file(args.local_file, final_settings)
    elif args.google_drive_link:
        if not credentials:
            print("[ERROR] Google API authentication is required for processing from Google Meet.")
            exit(1)
        file_id_to_download = extract_file_id_from_drive_link(args.google_drive_link)
        if file_id_to_download:
            download_meet_recording_to_temp_file(credentials, file_id_to_download, process_video_file, final_settings)
        else:
            print(
                "Could not extract a valid Google Drive File ID from the provided link. Please check the link format.")
            exit(1)
    elif args.list_recordings:
        if not credentials:
            print("[ERROR] Google API authentication is required for listing Google Meet recordings.")
            exit(1)
        available_recordings = list_meet_recordings(credentials)
        if available_recordings:
            print("\nAvailable Meet Recordings (in 'FILE_GENERATED' state):")
            for idx, rec in enumerate(available_recordings):
                print(f"  [{idx}] Conference: {rec['conference_record_id'].split('/')[-1]}, "
                      f"Recording: {rec['recording_id'].split('/')[-1]}, "
                      f"Drive File ID: {rec['file_id']}")
        else:
            print(
                "No Google Meet recordings found that are in 'FILE_GENERATED' state with a valid Drive File ID via the API.")
    else:
        parser.print_help()  # If no arguments provided, print help message


if __name__ == '__main__':
    main()
