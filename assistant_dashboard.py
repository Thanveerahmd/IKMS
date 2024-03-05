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

load_dotenv()

# Initialize Elasticsearch client
index_name = "search-ikms-assistants"

openai.api_key = os.environ["OPENAI_API_KEY"]

es = Elasticsearch(
    os.getenv('ES_END_POINT'),  # Elasticsearch endpoint
    api_key=os.getenv('ES_API_KEY'),  # API key ID and secret
)

client = OpenAI()


def fetch_all_assistants():
    """Fetch all assistant documents from Elasticsearch."""
    query = {"query": {"match_all": {}}}
    response = es.search(index=index_name, body=query, size=1000)
    assistants = [{"id": hit["_id"], **hit["_source"]} for hit in response["hits"]["hits"]]
    return assistants


def create_or_update_assistant(assistant_id, assistant_name, document_body):
    """Create or update an assistant in Elasticsearch."""
    if not es.indices.exists(index=index_name):
        es.indices.create(index=index_name)
    if assistant_id:  # Update existing assistant
        es.update(index=index_name, id=assistant_id, doc={"doc": document_body})
        st.success(f"Assistant {assistant_name} updated successfully!")
    else:  # Create new assistant
        es.index(index=index_name, document=document_body)
        st.success("Assistant created successfully!")


def delete_assistant(assistant_id):
    """Delete an assistant from Elasticsearch."""
    es.delete(index=index_name, id=assistant_id)
    st.success("Assistant deleted successfully.")


def assistant_form(assistants):
    """Form for adding or updating assistants."""
    assistant_id = st.selectbox("Choose an assistant to edit (select 'New' to add)",
                                ['New'] + [a['id'] for a in assistants])
    if assistant_id != 'New':
        assistant = next((a for a in assistants if a['id'] == assistant_id), None)
        if assistant:
            assistant_name = assistant['assistant_name']
            document_body = {k: v for k, v in assistant.items() if k not in ['id', 'assistant_name']}
        else:
            st.error("Selected assistant not found.")
            return
    else:
        assistant_name = ""
        document_body = {}

    assistant_name = st.text_input("Assistant Name", value=assistant_name)
    instructions = st.text_area("Enter instructions here")

    assistant = client.beta.assistants.create(
        name=assistant_name,
        instructions=instructions,
        tools=[{"type": "retrieval"}],
        model="gpt-4-turbo-preview",
    )

    document = {
        "assistant_id": assistant.id,
        "assistant_name": assistant_name
    }

    if st.button("Save Assistant"):
        if assistant_name:
            try:
                create_or_update_assistant(assistant_id if assistant_id != 'New' else None, assistant_name, document)
            except SyntaxError:
                st.error("Invalid format. Please enter a valid Python dictionary.")


def main():
    st.title("Assistant Management Dashboard")

    if st.button("Refresh Assistant List"):
        st.experimental_rerun()

    assistants = fetch_all_assistants()
    assistant_df = pd.DataFrame(assistants)
    if not assistant_df.empty:
        st.write("Assistants:")
        st.dataframe(assistant_df[['assistant_name']], width=700, height=300)

        selected_id = st.selectbox("Select an assistant to delete", options=[''] + list(assistant_df['id']))
        if selected_id:
            if st.button("Delete Assistant"):
                delete_assistant(selected_id)
                st.experimental_rerun()

    assistant_form(assistants)


if __name__ == "__main__":
    main()
