import streamlit as st
from elasticsearch import Elasticsearch
import pandas as pd
from dotenv import load_dotenv
import os
import pandas as pd
import os
import getpass
import openai
import time
import json
import re
from openai import OpenAI
from pathlib import Path

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


def setup_directories(base_dir="IKMS Data Repo"):
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    sub_dirs = ["Images", "Text", "Videos", "Audio"]
    for sub_dir in sub_dirs:
        Path(os.path.join(base_dir, sub_dir)).mkdir(parents=True, exist_ok=True)


def save_uploaded_file(directory, file):
    if file is not None:
        file_path = os.path.join(directory, file.name)
        with open(file_path, "wb") as f:
            f.write(file.getbuffer())
        st.success(f"Saved file: {file.name} in {directory}")
        return file_path
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


def clean_and_parse_json(input_text):
    # Remove markdown annotations (```json and ```)
    cleaned_text = re.sub(r'^```json|```$', '', input_text.strip(), flags=re.MULTILINE)

    # Attempt to fix common JSON formatting issues, like trailing commas
    cleaned_text = re.sub(r',\s*}', '}', cleaned_text)
    cleaned_text = re.sub(r',\s*\]', ']', cleaned_text)

    try:
        # Parse the cleaned text as JSON
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        return f"Error parsing JSON: {e}"


def search_assistant_by_name(assistant_name, index_name):
    """
    Search for an assistant by name in the specified index.

    :param assistant_name: The name of the assistant to search for.
    :return: A list of matching assistants.
    """
    query = {
        "query": {
            "match": {
                "assistant_name": assistant_name
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

    client.beta.assistants.update(
        matching_assistants[0]['assistant_id'],
        instructions=assistant.instructions,
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


def process_and_index_files(file_path, file_name, index, file_type, assistant_name):
    try:
        parsed_json = process_file(file_path, assistant_name)
        parsed_json['Source'] = file_path
        parsed_json['Document Name'] = file_name
        parsed_json['Type'] = file_type
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
            for image_file in image_files:
                st.image(image_file, caption=image_file.name)

    # Expander for text document uploads
    with st.expander("Upload Text Documents"):
        bulk_files = st.file_uploader("Choose multiple text files for bulk upload", type=['txt', 'pdf', 'docx'],
                                      accept_multiple_files=True, key="bulk")

        if bulk_files is not None:

            total_files = len(bulk_files)
            progress_bar = st.progress(0)

            for index, file in enumerate(bulk_files):
                with st.spinner(f'Processing and indexing {file.name}...'):
                    saved_file_path = save_uploaded_file("IKMS Data Repo/Text", file)
                    if saved_file_path:  # If the file was successfully saved
                        process_and_index_files(saved_file_path, file.name, document_index, "Text", "Meta Doc Creator")
                st.success(f'Finished processing {file.name}')

                progress_percentage = int(((index + 1) / total_files) * 100)
                progress_bar.progress(progress_percentage)

            st.success("Finished processing all files.")

    # Expander for video uploads
    with st.expander("Upload Videos"):
        video_files = st.file_uploader("Choose video files", type=['mp4', 'mov', 'avi'], accept_multiple_files=True)
        if video_files is not None:
            for video_file in video_files:
                st.video(video_file)

    # Expander for audio uploads
    with st.expander("Upload Audios"):
        audio_files = st.file_uploader("Choose audio files", type=['mp3', 'wav'], accept_multiple_files=True)
        if audio_files is not None:
            for audio_file in audio_files:
                st.audio(audio_file)


if __name__ == "__main__":
    main()
