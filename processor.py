import json
import os
import re
from collections import Counter
from datetime import timedelta

import requests
import whisper
import cv2
import easyocr

from tqdm import tqdm

from settings import DEFAULT_SETTINGS

# constants for optimal voice-video processing
FRAME_INTERVAL = 1  # the number of OCR reads per second of video footage
VOICE_TIMESTAMP_OFFSET = 2.5  # time (in seconds) between speaker change and camera change in Google Meet
PRIMARY_CROP_BOX = (0.0, 0.9, 0.2, 0.1)  # the crop box to extract the speaker name from bottom left corner
SECONDARY_CROP_BOX = (0.7, 0.5, 0.2, 0.2)  # the crop box to extract the speaker name from the right tile during screen sharing


def extract_transcribed_words(video_path):
    model = whisper.load_model("base")
    result = model.transcribe(video_path, word_timestamps=True, fp16=False, verbose=False)

    segments = result["segments"]
    words = []
    for segment in segments:
        words.extend(segment.get("words", []))

    return segments, words


def is_valid_name(name):
    # Accepts names with 2+ capitalized words including accented characters, apostrophes, and hyphens
    return re.fullmatch(r"^([A-ZÀ-Ý][a-zà-ÿ]*(?:['-][A-ZÀ-Ý][a-zà-ÿ]+)*\s)+[A-ZÀ-Ý][a-zà-ÿ]*(?:['-][A-ZÀ-Ý][a-zà-ÿ]+)*$", name) is not None


def extract_speaker_labels(video_path, message_queue=None):
    reader = easyocr.Reader(['en'])
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_skip = int(fps * FRAME_INTERVAL)
    frame_idx = 0
    results = []
    last_valid_speaker = None

    # Get video dimensions
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    total_checks = total_frames // frame_skip

    def get_crop_box(crop_rel):
        # extract an absolute crop box from a given relative one
        x_rel, y_rel, w_rel, h_rel = crop_rel
        return int(width * x_rel), int(height * y_rel), int(width * w_rel), int(height * h_rel)

    primary_box = get_crop_box(PRIMARY_CROP_BOX)
    secondary_box = get_crop_box(SECONDARY_CROP_BOX)

    if message_queue:
        message_queue.put(("progress_init", "OCR in Progress..."))  # Signal to init progress bar
    with tqdm(total=total_checks, desc="OCR Progress", unit="frame") as pbar:
        while cap.isOpened():
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            timestamp = frame_idx / fps
            if VOICE_TIMESTAMP_OFFSET is not None:
                timestamp = max(0.0, timestamp - VOICE_TIMESTAMP_OFFSET)

            def try_ocr(box):
                x, y, w, h = box
                cropped = frame[y:y+h, x:x+w]
                ocr_result = reader.readtext(cropped)
                if ocr_result:
                    return ocr_result[0][-2].strip()
                return None

            text = try_ocr(primary_box)
            if not text and secondary_box:
                text = try_ocr(secondary_box)

            if text:
                # a hack addressing an annoying easyocr bug where big O is recognized as 0 (zero)
                text = text.replace("0", "O")

                if is_valid_name(text):
                    results.append((timestamp, text))
                    last_valid_speaker = text
                elif last_valid_speaker is not None:
                    results.append((timestamp, last_valid_speaker))

            elif last_valid_speaker is not None:
                results.append((timestamp, last_valid_speaker))

            frame_idx += frame_skip
            pbar.update(1)
            if message_queue:
                # Send progress updates to the queue
                progress = int(min(1.0, frame_idx / total_frames) * 100)
                message_queue.put(("progress_update", progress))

    cap.release()
    if message_queue:
        message_queue.put(("progress_complete", None))  # Signal completion of progress bar
    return filter_noisy_speakers(results)


def filter_noisy_speakers(speaker_timeline):
    filtered_speaker_timeline = []
    for i, record in enumerate(speaker_timeline):
        if i == 0 or i == len(speaker_timeline) - 1:
            filtered_speaker_timeline.append(record)
            continue

        current_speaker = record[1]
        prev_speaker = speaker_timeline[i-1][1]
        next_speaker = speaker_timeline[i+1][1]
        if current_speaker != prev_speaker and current_speaker != next_speaker:
            continue

        filtered_speaker_timeline.append(record)

    return filtered_speaker_timeline


def extract_speech_intervals(segments, words):
    switch_points = []
    prev_end_time = None

    silence_threshold = 2.0

    for word in words:
        word_start = word["start"]
        word_end = word["end"]

        if prev_end_time is None:
            # first interval
            switch_points.append(word_start)
        elif word_start - prev_end_time >= silence_threshold:
            switch_points.append(word_start)
        prev_end_time = word_end

    # push the last interval end
    switch_points.append(prev_end_time)

    segment_switch_points = []
    for segment in segments:
        segment_switch_points.extend([segment["start"], segment["end"]])

    all_switch_points = sorted(set(segment_switch_points))

    return [(all_switch_points[i], all_switch_points[i+1]) for i in range(len(all_switch_points) - 1)]


def get_majority_speaker(speakers):
    if not speakers:
        return None

    if len(speakers) == 1:
        return speakers[0]

    counter = Counter(speakers)
    most_common = counter.most_common(1)
    return most_common[0][0] if most_common else None


def assign_speakers_to_intervals(intervals, speaker_timeline):
    interval_to_speaker = {}
    for interval in intervals:
        speakers = list(set([s[1] for s in speaker_timeline if interval[0] <= s[0] <= interval[1]]))
        interval_to_speaker[interval] = get_majority_speaker(speakers)
    return interval_to_speaker


def get_current_speaker(current_timestamp, intervals, speaker_timeline):
    candidates = [s for s in speaker_timeline if s[0] <= current_timestamp]
    return candidates[-1][1] if candidates else speaker_timeline[0][1]


def summarize_transcript(transcript, settings):
    summary_prompt = settings["summary_prompt_format"].format(transcript)
    payload = {
        "model": settings["vllm_model_id"],
        "messages": [
            {"role": "system", "content": settings["system_prompt"]},
            {"role": "user", "content": summary_prompt}
        ],
        "stream": False
    }
    response = requests.post(f"{settings['vllm_url']}/v1/chat/completions", json=payload)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def seconds_to_hms(seconds):
    return str(timedelta(seconds=int(seconds)))


def transcribe_with_speakers(video_path, settings, message_queue=None):
    def _send_msg(msg_type, msg_content):
        if message_queue:
            message_queue.put((msg_type, msg_content))
        else:
            print(f"[Processor] {msg_content}")

    _send_msg("info", "Extracting speaker labels using OCR...")
    speaker_timeline = extract_speaker_labels(video_path, message_queue)
    _send_msg("success", f"Successfully extracted speaker labels from {video_path}")

    _send_msg("info", "Transcribing audio...")
    segments, words = extract_transcribed_words(video_path)
    _send_msg("success", f"Successfully transcribed audio from {video_path}")

    intervals = extract_speech_intervals(segments, words)

    transcript_lines = []
    current_speaker = None
    current_text = ""
    start_time = None

    for word in words:
        word_start = word["start"]
        word_text = word["word"]
        speaker = get_current_speaker(word_start, intervals, speaker_timeline)

        if speaker != current_speaker:
            if current_speaker is not None:
                timestamp = seconds_to_hms(start_time)
                transcript_lines.append((timestamp, current_speaker, current_text.strip()))
            current_speaker = speaker
            current_text = word_text
            start_time = word_start
        else:
            current_text += word_text

    if current_speaker is not None:
        timestamp = seconds_to_hms(start_time)
        transcript_lines.append((timestamp, current_speaker, current_text.strip()))

    output_dir_path = os.path.expanduser(settings["output_dir"])

    transcript_path = os.path.join(output_dir_path, settings["transcript_file_name"])
    with open(transcript_path, 'w', encoding='utf-8') as f:
        for timestamp, speaker, text in transcript_lines:
            f.write(f"[{timestamp}] {speaker}: {text}\n")

    _send_msg("success", f"Speaker-attributed transcript saved to: {transcript_path}")

    if not settings["summarize_transcript"]:
        return

    _send_msg("info", f"Generating transcript summary...")

    full_transcript = "\n".join([f"{speaker}: {text}" for _, speaker, text in transcript_lines])
    summary = summarize_transcript(full_transcript, settings)

    summary_path = os.path.join(output_dir_path, settings["summary_file_name"])
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(summary)

    _send_msg("success", f"Summary saved to: {summary_path}")


def process_video_file(video_path, settings=DEFAULT_SETTINGS, message_queue=None):
    def _send_msg(msg_type, msg_content):
        if message_queue:
            message_queue.put((msg_type, msg_content))
        print(f"[Processor] {msg_content}")

    _send_msg("info", f"Starting video processing for file: `{video_path}`")
    _send_msg("text", f"Using settings: \n{json.dumps(settings, indent=2)}")

    try:
        transcribe_with_speakers(video_path, settings, message_queue)

        _send_msg("info", f"Finished processing file: {video_path}")
    except FileNotFoundError:
        error_msg = f"Error: File not found at `{video_path}`"
        _send_msg("error", error_msg)
    except Exception as e:
        error_msg = f"An unexpected error occurred during processing: {e}"
        _send_msg("error", error_msg)
