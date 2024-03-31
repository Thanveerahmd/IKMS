import json
import os
import re
import time
import base64
from pathlib import Path

import openai
import streamlit as st
from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from moviepy.editor import AudioFileClip, ImageClip
from moviepy.editor import VideoFileClip
from openai import OpenAI
from pydub import AudioSegment
from pytube import YouTube
from videodb import connect

from prompts import image_meta_summarizer

load_dotenv()

# Initialize Elasticsearch client
assistant_index = "ikms-assistants"
document_index = "meta-summary-registry"

openai.api_key = os.environ["OPENAI_API_KEY"]

es = Elasticsearch(
    os.getenv('ES_END_POINT'),  # Elasticsearch endpoint
    api_key=os.getenv('ES_API_KEY'),  # API key ID and secret
)

client = OpenAI()
video_db_conn = connect(api_key=os.getenv('VIDEO_DB_API_KEY'))


def convert_mp3_to_mp4_with_image(audio_file_path, image_file_path, output_video_path):
    # Load the audio file
    audio_clip = AudioFileClip(audio_file_path)

    # Load the image file and set its duration to match the audio clip's duration
    image_clip = ImageClip(image_file_path).set_duration(audio_clip.duration)

    # Set the audio of the image clip to be the audio clip
    video_clip = image_clip.set_audio(audio_clip)

    # Write the result to a file (the output video file)
    video_clip.write_videofile(output_video_path, fps=1)  # fps can be low since the image is static

    # Close the clips to free up system resources
    audio_clip.close()
    video_clip.close()


def sanitize_filename(title):
    """Sanitize the title to make it a valid filename according to specific rules.
    Replaces spaces and some punctuation with underscores, keeps certain characters like '&' and '()',
    and limits the length of the filename.
    """
    # First, replace dots, commas, and hyphens with nothing
    title = re.sub(r'[.,-]', '', title)

    # Replace sequences of whitespace with a single underscore, except around "&" and before "(" or ")"
    # This also implicitly handles cases of multiple spaces
    sanitized = re.sub(r'\s+(?![&()])|(?<![&()])\s+', '_', title)

    # Replace invalid filename characters with "_", excluding "&" and "()"
    # Note: At this point, only characters we want to keep or have already handled should remain,
    # but this is kept for safety against other potentially invalid characters
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", sanitized)

    # Optionally, truncate to a maximum length, e.g., 255 characters
    max_length = 255
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    return sanitized


def download_video(url, output_path):
    yt = YouTube(url)
    video_title = sanitize_filename(yt.title)
    filename_with_extension = f"{video_title}.mp4"  # Include extension for clarity

    # Ensure the output directory exists
    Path(output_path).mkdir(parents=True, exist_ok=True)

    file_path = os.path.join(output_path, filename_with_extension)  # Corrected to use filename with extension
    st.write(output_path)
    # Download the video
    yt.streams.get_highest_resolution().download(output_path=output_path,
                                                 filename=filename_with_extension)  # Keep filename without extension for download method

    # Update metadata to reflect the actual file path
    metadata = {
        "Author": yt.author,
        "Title": yt.title,
        "Views": yt.views,
        "FilePath": file_path  # Correct file path in metadata
    }

    return metadata


def video_to_audio(video_path, output_folder):
    st.write(video_path)
    video_title = os.path.splitext(os.path.basename(video_path))[0]
    output_audio_path = os.path.join(output_folder, f"{sanitize_filename(video_title)}.wav")

    clip = VideoFileClip(video_path)
    audio = clip.audio
    audio.write_audiofile(output_audio_path)

    return output_audio_path


def split_audio(audio_path, segment_length=10 * 60 * 1000):
    audio = AudioSegment.from_file(audio_path)
    segments = []
    base_audio_path = audio_path.rsplit(".", 1)[0]  # Removes file extension

    for i in range(0, len(audio), segment_length):
        segment_path = f"{base_audio_path}_segment_{i // segment_length}.mp3"
        audio[i:i + segment_length].export(segment_path, format="mp3")
        segments.append(segment_path)

    return segments


def audio_to_text(audio_path, is_audio=False):
    segments = split_audio(audio_path)
    combined_text = []

    for segment_path in segments:
        with open(segment_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
            combined_text.append(transcription.text)

    for segment_path in segments:
        os.remove(segment_path)  # Clean up segment after processing

    # Optionally, clean up the original audio file after processing all segments
    if not is_audio:
        os.remove(audio_path)

    return " ".join(combined_text)


def setup_directories(base_dir="IKMS Data Repo"):
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    sub_dirs = ["Image", "Text", "Video", "Audio"]
    for sub_dir in sub_dirs:
        Path(os.path.join(base_dir, sub_dir)).mkdir(parents=True, exist_ok=True)


def save_uploaded_file(directory, file):
    if file is not None:
        # Split the filename into base name and extension
        base_name, extension = os.path.splitext(file.name)
        # Sanitize the base name
        sanitized_base_name = sanitize_filename(base_name)
        # Combine the sanitized base name with the original extension
        new_file_name = sanitized_base_name + extension
        file_path = os.path.join(directory, new_file_name)
        with open(file_path, "wb") as f:
            f.write(file.getbuffer())
        st.success(f"Saved file: {new_file_name} in {directory}")
        return file_path, sanitized_base_name
    return None


def wait_for_complete(run, thread, client):
    while run.status == "queued" or run.status == "in_progress":
        run = client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id,
        )
        time.sleep(0.5)
    return run


def process_replies(replies, client):
    citations = []
    full_response = ""

    for r in replies:

        if r.role == "assistant":

            message_content = r.content[0].text
            annotations = message_content.annotations

            for index, annotation in enumerate(annotations):
                message_content.value = message_content.value.replace(
                    annotation.text, f"【{index}†source】"
                )
                if file_citation := getattr(annotation, "file_citation", None):
                    cited_file = client.files.retrieve(file_citation.file_id)
                    citations.append(
                        f"【{index}†source】 {file_citation.quote} from {cited_file.filename}"
                    )
                elif file_path := getattr(annotation, "file_path", None):
                    cited_file = client.files.retrieve(file_path.file_id)
                    citations.append(
                        f"【{index}†source】 Click <here> to download {cited_file.filename}"
                    )

            full_response += message_content.value + "\n"
            break

    full_response += "\n".join(citations)

    return full_response


def clean_and_parse_json(text, start_index=0):
    objects = []
    stack = []
    current_object_start = None

    for i in range(start_index, len(text)):
        if text[i] == '{':
            if not stack:
                current_object_start = i  # Mark the start of a new object
            stack.append('{')
        elif text[i] == '}':
            stack.pop()
            if not stack and current_object_start is not None:
                # We've found an object, try to parse it to JSON
                object_str = text[current_object_start:i + 1]
                try:
                    parsed_object = json.loads(object_str)
                    objects.append(parsed_object)
                    # No recursive call needed, as json.loads will handle nested structures internally
                except json.JSONDecodeError:
                    print(f"Error decoding JSON from: {object_str}")
                current_object_start = None

    return objects[0]


def search_assistant_by_name(assistant_name, index_name):
    """
    Search for an assistant by name in the specified index using an exact match.

    :param assistant_name: The name of the assistant to search for.
    :param index_name: The name of the index to search within.
    :return: A list of matching assistants with the exact given name.
    """
    query = {
        "query": {
            "term": {
                "assistant_name.keyword": assistant_name  # Use the .keyword for exact matches on text fields
            }
        }
    }

    try:
        response = es.search(index=index_name, body=query)
        hits = response['hits']['hits']
        return [hit["_source"] for hit in hits]
    except Exception as e:
        print(f"An error occurred: {e}")
        return []


def process_file(file_path, assistant_name):
    file = client.files.create(
        file=open(file_path, 'rb'),
        purpose="assistants"
    )

    matching_assistants = search_assistant_by_name(assistant_name, assistant_index)

    if not matching_assistants:
        print("Assistant not found.")
        return

    assistant = client.beta.assistants.retrieve(matching_assistants[0]['assistant_id'])

    prompt = matching_assistants[0]['Prompt']

    client.beta.assistants.update(
        matching_assistants[0]['assistant_id'],
        instructions=prompt,
        name=assistant.name,
        tools=[{"type": "retrieval"}],
        model="gpt-4-turbo-preview",
        file_ids=[file.id],
    )

    thread = client.beta.threads.create()

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content="Extract",
    )

    # Implement wait_for_complete and process_replies functions as before

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id,
    )

    wait_for_complete(run, thread, client)

    replies = client.beta.threads.messages.list(
        thread_id=thread.id
    )

    text = process_replies(replies, client)

    parsed_json = clean_and_parse_json(text)

    return parsed_json


def get_unique_sys_keywords():
    keywords_query = {
        "size": 0,
        "aggs": {
            "nested_metadata": {
                "nested": {
                    "path": "Metadata"
                },
                "aggs": {
                    "unique_doc_keywords": {
                        "terms": {
                            "field": "Metadata.DOC_Keywords",
                            "size": 10000
                        }
                    }
                }
            }
        }
    }

    keywords_response = es.search(index=document_index, body=keywords_query)
    # Extract the list of unique document keywords from the response
    unique_sys_keywords = [bucket['key'] for bucket in
                           keywords_response['aggregations']['nested_metadata']['unique_doc_keywords']['buckets']]

    return unique_sys_keywords


def get_unique_sys_domains():
    domains_query = {
        "size": 0,
        "aggs": {
            "nested_metadata": {
                "nested": {
                    "path": "Metadata"
                },
                "aggs": {
                    "unique_domains": {
                        "terms": {
                            "field": "Metadata.Domain.keyword",
                            "size": 10000
                        }
                    }
                }
            }
        }
    }

    domains_response = es.search(index=document_index, body=domains_query)

    # Extract the list of unique domains from the response
    unique_sys_domains = [bucket['key'] for bucket in
                          domains_response['aggregations']['nested_metadata']['unique_domains']['buckets']]
    return unique_sys_domains


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def process_image_file(image_path):
    base64_image = encode_image(image_path)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": image_meta_summarizer},
                {
                    "type": "image_url",
                    "image_url": f"data:image/jpeg;base64,{base64_image}",
                },
            ],
        }
    ]

    try:
        response = client.chat.completions.create(
            model="gpt-4-vision-preview", messages=messages, max_tokens=500
        )
        text = response.choices[0].message.content

        parsed_json = clean_and_parse_json(text)

        return parsed_json

    except Exception as e:
        print(f"An error occurred: {e}")


def process_and_index_files(file_path, file_name, index, file_type, assistant_name=None, vedio_id=None, file_loc=None):
    try:

        if assistant_name is not None:
            parsed_json = process_file(file_path, assistant_name)
        else:
            parsed_json = process_image_file(file_path)

        parsed_json['Document Source'] = file_path
        parsed_json['Document Name'] = file_name
        parsed_json['Data Type'] = file_type

        if vedio_id is not None:
            parsed_json[file_type + ' Source'] = file_loc
            parsed_json['Video Id'] = vedio_id  # Only add if vedio_id is provided

        # Index the document into Elasticsearch
        es.index(index=index, document=parsed_json)
        st.write(f"Processed and indexed {file_name}")

    except Exception as e:
        st.error(f"Error processing {file_name}: {str(e)}")


def main():
    st.title("Data Ingestion Pipeline")

    setup_directories(base_dir="IKMS Data Repo")

    # Expander for image uploads
    with st.expander("Upload Images"):
        image_files = st.file_uploader("Choose image files", type=['png', 'jpg', 'jpeg'], accept_multiple_files=True)

        if image_files is not None:
            total_files = len(image_files)
            progress_bar = st.progress(0)

            for index, file in enumerate(image_files):

                st.image(file, caption=file.name)

                with st.spinner(f'Processing and indexing {file.name}...'):

                    saved_file_path, title = save_uploaded_file("IKMS Data Repo/Image", file)

                    if saved_file_path:  # If the file was successfully saved
                        process_and_index_files(saved_file_path, file.name, document_index, "Image")

                st.success(f'Finished processing Image {file.name}')

                progress_percentage = int(((index + 1) / total_files) * 100)
                progress_bar.progress(progress_percentage)

            st.success("Finished processing all the Images.")

    # Expander for text document uploads
    with st.expander("Upload Text Documents"):
        bulk_files = st.file_uploader("Choose multiple text files for bulk upload", type=['txt', 'pdf', 'docx'],
                                      accept_multiple_files=True, key="bulk")

        if bulk_files is not None:

            total_files = len(bulk_files)
            progress_bar = st.progress(0)

            for index, file in enumerate(bulk_files):
                with st.spinner(f'Processing and indexing {file.name}...'):
                    saved_file_path, title = save_uploaded_file("IKMS Data Repo/Text", file)
                    if saved_file_path:  # If the file was successfully saved
                        process_and_index_files(saved_file_path, file.name, document_index, "Text", "Meta Doc Creator")
                st.success(f'Finished processing {file.name}')

                progress_percentage = int(((index + 1) / total_files) * 100)
                progress_bar.progress(progress_percentage)

            st.success("Finished processing all files.")

    # Expander for video uploads
    with st.expander("Upload Videos"):
        video_file = st.file_uploader("Choose video file", type=['mp4', 'mov', 'avi'], accept_multiple_files=False)
        if video_file is not None:

            st.video(video_file)
            video_path = "IKMS Data Repo/Video"

            saved_file_path, title = save_uploaded_file(video_path, video_file)

            if saved_file_path:

                st.success(f"Successfully saved {video_file.name} to {video_path}/")

                with st.spinner('Extracting audio from video...'):
                    try:
                        output_audio_path = video_to_audio(saved_file_path, "IKMS Data Repo/Audio")
                        st.success('Audio extracted successfully!')
                    except Exception as e:
                        st.error(f"Error extracting audio: {e}")
                        raise e  # Re-raise exception if needed

                with st.spinner('Transcribing audio to text...'):
                    try:
                        text_data = audio_to_text(output_audio_path)
                        text_output_path = "IKMS Data Repo/Text/" + title + "_text.txt"
                        with open(text_output_path, "w") as file:
                            file.write(text_data)
                        st.success(f'Text data saved to file: {text_output_path}')
                        video = video_db_conn.upload(file_path=saved_file_path)
                        video_id = video.id
                        process_and_index_files(text_output_path,
                                                file.name,
                                                document_index,
                                                "Video",
                                                "Meta Transcript Creator",
                                                vedio_id=video_id,
                                                file_loc=saved_file_path)
                    except Exception as e:
                        st.error(f"Error during transcription: {e}")
            else:
                st.error("Failed to save the file.")

        st.divider()

        video_url = st.text_input("Paste video URL here", "")

        if video_url:
            # Assuming the URL directly points to a video file that Streamlit can embed
            st.video(video_url)

            with st.spinner('Downloading video...'):
                try:
                    video_metadata = download_video(video_url, "IKMS Data Repo/Video")
                    st.success('Video downloaded successfully!')
                except Exception as e:
                    st.error(f"Error downloading video: {e}")
                    raise e  # Re-raise exception if you need to stop the process here

            with st.spinner('Extracting audio from video...'):
                try:
                    output_audio_path = video_to_audio(video_metadata["FilePath"], "IKMS Data Repo/Audio")
                    st.success('Audio extracted successfully!')
                except Exception as e:
                    st.error(f"Error extracting audio: {e}")
                    raise e  # Re-raise exception if needed

            with st.spinner('Transcribing audio to text...'):
                try:
                    text_data = audio_to_text(output_audio_path)
                    text_output_path = "IKMS Data Repo/Text/" + sanitize_filename(video_metadata["Title"]) + "_text.txt"
                    with open(text_output_path, "w") as file:
                        file.write(text_data)
                    st.success(f'Text data saved to file: {text_output_path}')
                    video = video_db_conn.upload(url=video_url)
                    video_id = video.id
                    process_and_index_files(text_output_path,
                                            file.name,
                                            document_index,
                                            "Video",
                                            "Meta Transcript Creator",
                                            vedio_id=video_id,
                                            file_loc=video_metadata["FilePath"]
                                            )
                except Exception as e:
                    st.error(f"Error during transcription: {e}")

    # Expander for audio uploads
    with st.expander("Upload Audio files"):

        audio_path = "IKMS Data Repo/Audio/"
        audio_files = st.file_uploader("Choose audio files", type=['mp3', 'wav'], accept_multiple_files=True)

        if audio_files is not None:
            for audio_file in audio_files:
                with st.spinner('Uploading audio...'):
                    saved_file_path, title = save_uploaded_file(audio_path, audio_file)
                    st.success('Audio uploaded successfully.')

                # Generate video file path
                output_directory = "IKMS Data Repo/Video"
                output_video_file = os.path.join(output_directory, f"{title}.mp4")

                # Load the audio and image clips
                with st.spinner('Processing audio for video creation...'):
                    convert_mp3_to_mp4_with_image(
                        audio_file_path=saved_file_path,
                        image_file_path='assets/audio/audio_img.png',
                        output_video_path=output_video_file
                    )
                    st.success(f'Video file created: {output_video_file}')

                    video = video_db_conn.upload(file_path=output_video_file)
                    video_id = video.id

                    os.remove(output_video_file)

                # Transcribe audio to text
                with st.spinner('Transcribing audio to text...'):
                    text_data = audio_to_text(saved_file_path, is_audio=True)
                    text_output_path = "IKMS Data Repo/Text/" + title + "_text.txt"

                    with open(text_output_path, 'w', encoding='utf-8') as file:
                        file.write(text_data)

                    st.success(f'Text data saved to file: {text_output_path}')

                process_and_index_files(text_output_path,
                                        file.name,
                                        document_index,
                                        "Audio",
                                        "Meta Transcript Creator",
                                        vedio_id=video_id,
                                        file_loc=saved_file_path)

                # Final success message for the entire process
                st.success(f"Processing complete. Audio, Video and text files saved.")


if __name__ == "__main__":
    main()
