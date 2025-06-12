DEFAULT_SETTINGS = {
    "output_dir": "~/Documents",
    "transcript_file_name": "transcript.txt",
    "summarize_transcript": True,
    "summary_file_name": "summary.txt",
    "vllm_url": "http://localhost:8000",
    "vllm_model_id": "granite32-8b",
    "system_prompt": ("You are an expert meeting assistant. Your job is to analyze meeting transcripts, "
                      "extract important insights, identify decisions, and list clear action items for the participants."),
    "summary_prompt_format": """Please analyze the following meeting transcript:
    
TRANSCRIPT
{}
END OF TRANSCRIPT
 
Now, produce the following:
1. A concise summary of the meeting.
2. Key discussion points.
3. Notable decisions made.
4. Action items with responsible persons if mentioned.

Be accurate, structured, and clear. Use bullet points where helpful.
""",
}

SETTING_DESCRIPTIONS = {
    "output_dir": "Directory where processed output files (meeting summary and full transcript) will be stored.",
    "transcript_file_name": "The name of the file to contain the generated transcript.",
    "summarize_transcript": "True to summarize the meeting transcript (requires a running vLLM instance) and False to omit the summarization",
    "summary_file_name": "The name of the file to contain the generated meeting summary.",
    "vllm_url": "The URL of a running vLLM instance.",
    "vllm_model_id": "The ID of the model to use for summarization",
    "system_prompt": "The system prompt to use for summarization",
    "summary_prompt_format": "The prompt to be sent as the summarization request. Must contain a placeholder ('{}') for the transcript.",
}
