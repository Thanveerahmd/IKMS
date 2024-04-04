import ast
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Dict

import chainlit as cl
from chainlit.element import Element
from chainlit.types import ThreadDict
from elasticsearch import Elasticsearch
from openai import AsyncOpenAI
from openai.types.beta import Thread
from openai.types.beta.threads import (
    MessageContentImageFile,
    MessageContentText,
    ThreadMessage,
)
from openai.types.beta.threads.runs import RunStep
from openai.types.beta.threads.runs.tool_calls_step_details import ToolCall

from prompts import es_retrieve_query_prompt

api_key = os.environ.get("OPENAI_API_KEY")
assistant_id = os.environ.get("ASSISTANT_ID")
assistant_index = "ikms-assistants"
document_index = "meta-summary-registry"

# Instrument the OpenAI client
client = AsyncOpenAI(api_key=api_key)
cl.instrument_openai()

# List of allowed mime types
allowed_mime = ["text/csv", "application/pdf"]

es = Elasticsearch(
    os.getenv('ES_END_POINT'),  # Elasticsearch endpoint
    api_key=os.getenv('ES_API_KEY'),  # API key ID and secret
)


@cl.oauth_callback
def oauth_callback(
        provider_id: str,
        token: str,
        raw_user_data: Dict[str, str],
        default_user: cl.User,
) -> Optional[cl.User]:
    return default_user


@cl.author_rename
def rename(orig_author: str):
    rename_dict = {"Chatbot": "Osiris", "assistant": "Osiris"}
    return rename_dict.get(orig_author, orig_author)


# Check if the files uploaded are allowed
async def check_files(files: List[Element]):
    for file in files:
        if file.mime not in allowed_mime:
            return False
    return True


# Upload files to the assistant
async def upload_files(files: List[Element]):
    file_ids = []
    for file in files:
        uploaded_file = await client.files.create(
            file=Path(file.path), purpose="assistants"
        )
        file_ids.append(uploaded_file.id)
    return file_ids


# Upload files to the assistant
@cl.step(name="RAG Builder", root=True)
async def upload_files_from_path(file_paths):
    file_ids = []
    for file_path in file_paths:
        uploaded_file = await client.files.create(
            file=Path(file_path), purpose="assistants"
        )
        file_ids.append(uploaded_file.id)
    return file_ids


async def process_files(files: List[Element]):
    # Upload files if any and get file_ids
    file_ids = []
    if len(files) > 0:
        files_ok = await check_files(files)

        if not files_ok:
            file_error_msg = f"Hey, it seems you have uploaded one or more files that we do not support currently, " \
                             f"please upload only : {(',').join(allowed_mime)} "
            await cl.Message(content=file_error_msg).send()
            return file_ids

        file_ids = await upload_files(files)

    return file_ids


async def process_thread_message(
        message_references: Dict[str, cl.Message], thread_message: ThreadMessage,
file_sources : Dict[str, str]
):
    elements = []  # Initialize the elements list

    # Check each data type and add corresponding element(s) to the elements list
    for data_type, sources in file_sources.items():
        for source in sources:
            if data_type == "text":
                elements.append(cl.Pdf(name=source, display="inline", path=source))
            elif data_type == "image":
                elements.append(cl.Image(path=source, name=source, display="inline"))
            elif data_type == "audio":
                elements.append(cl.Audio(name=source, path=source, display="inline"))
            elif data_type == "video":
                elements.append(cl.Video(name=source, path=source, display="inline"))


    for idx, content_message in enumerate(thread_message.content):
        id = thread_message.id + str(idx)
        if isinstance(content_message, MessageContentText):
            if id in message_references:
                msg = message_references[id]
                msg.content = content_message.text.value
                await msg.update()
            else:
                message_references[id] = cl.Message(
                    author=thread_message.role,
                    content=content_message.text.value,
                    elements=elements,
                )
                await message_references[id].send()
        elif isinstance(content_message, MessageContentImageFile):
            image_id = content_message.image_file.file_id
            response = await client.files.with_raw_response.retrieve_content(image_id)
            elements = [
                cl.Image(
                    name=image_id,
                    content=response.content,
                    display="inline",
                    size="large",
                ),
            ]

            if id not in message_references:
                message_references[id] = cl.Message(
                    author=thread_message.role,
                    content="",
                    elements=elements,
                )
                await message_references[id].send()
        else:
            print("unknown message type", type(content_message))


async def process_tool_call(
        step_references: Dict[str, cl.Step],
        step: RunStep,
        tool_call: ToolCall,
        name: str,
        input: Any,
        output: Any,
        show_input: str = None,
):
    cl_step = None
    update = False
    if not tool_call.id in step_references:
        cl_step = cl.Step(
            name=name,
            type="tool",
            parent_id=cl.context.current_step.id,
            show_input=show_input,
        )
        step_references[tool_call.id] = cl_step
    else:
        update = True
        cl_step = step_references[tool_call.id]

    if step.created_at:
        cl_step.start = datetime.fromtimestamp(step.created_at).isoformat()
    if step.completed_at:
        cl_step.end = datetime.fromtimestamp(step.completed_at).isoformat()
    cl_step.input = input
    cl_step.output = output

    if update:
        await cl_step.update()
    else:
        await cl_step.send()


class DictToObject:
    def __init__(self, dictionary):
        for key, value in dictionary.items():
            if isinstance(value, dict):
                setattr(self, key, DictToObject(value))
            else:
                setattr(self, key, value)

    def __str__(self):
        return "\n".join(f"{key}: {value}" for key, value in self.__dict__.items())


@cl.on_chat_start
async def start_chat():
    thread = await client.beta.threads.create()
    cl.user_session.set("thread", thread)
    app_user = cl.user_session.get("user")
    await cl.Message(f"Hello {app_user.identifier}").send()


@cl.step(name="Document Search Query Builder", root=True)
async def search_documents(user_query: str):
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

    keywords_response = es.search(index=document_index, body=keywords_query)
    # Extract the list of unique document keywords from the response
    unique_doc_keywords = [bucket['key'] for bucket in
                           keywords_response['aggregations']['nested_metadata']['unique_doc_keywords']['buckets']]

    domains_response = es.search(index=document_index, body=domains_query)

    # Extract the list of unique domains from the response
    unique_domains = [bucket['key'] for bucket in
                      domains_response['aggregations']['nested_metadata']['unique_domains']['buckets']]

    prompt = es_retrieve_query_prompt.format(keywords=unique_doc_keywords,
                                             domains=unique_domains,
                                             user_query=user_query)

    agent_response = await client.chat.completions.create(
        model="gpt-4-turbo-preview",
        messages=[
            {"role": "system", "content": prompt},
        ]
    )

    # Converting the string representation into a dictionary
    data_dict = ast.literal_eval(agent_response.choices[0].message.content)

    # Converting the string representations of lists within the dictionary back to lists
    for key, value in data_dict.items():
        data_dict[key] = ast.literal_eval(value)

    return data_dict


@cl.step(name="Document filter Agent", root=True)
async def filter_documents(data_dict: Dict):
    should_clauses = [{"match": {"Metadata.Domain": domain}} for domain in data_dict['Domains']]

    query_map = {
        "query": {
            "bool": {
                "must": [
                    {
                        "nested": {
                            "path": "Metadata",
                            "query": {
                                "bool": {
                                    "should": should_clauses
                                }
                            }
                        }
                    },
                    {
                        "nested": {
                            "path": "Metadata",
                            "query": {
                                "bool": {
                                    "must": [
                                        {
                                            "terms": {
                                                "Metadata.DOC_Keywords": data_dict['Keywords']
                                            }
                                        }
                                    ]
                                }
                            }
                        }
                    }
                ]
            }
        }
    }

    filtered_documents = es.search(index=document_index, body=query_map)
    hits_list = filtered_documents['hits']['hits']
    data_list = [{
        "Document Id": hit['_id'],
        "Document Name": hit['_source']['Document Name'],
        "Document path": hit['_source']['Document Source']
    } for hit in hits_list]

    document_paths = [document["Document path"] for document in data_list]

    # Initialize file_sources as a dictionary
    file_sources = {
        "text": [],
        "image": [],
        "audio": [],
        "video": []
    }

    # Loop through each document in the hits list
    for hit in hits_list:
        # Fetch the Data Type for the current document
        data_type = hit['_source'].get("Data Type", "").lower()  # Make case insensitive

        # Conditional checks to decide which source value to add
        if data_type in ["text", "image"]:
            source = hit['_source'].get("Document Source", None)
            if source:  # Ensure source is not None or empty before appending
                file_sources[data_type].append(source)
        elif data_type == "audio":
            source = hit['_source'].get("Audio Source", None)
            if source:
                file_sources[data_type].append(source)
        elif data_type == "video":
            source = hit['_source'].get("Video Source", None)
            if source:
                file_sources[data_type].append(source)

    return document_paths, file_sources


@cl.step(name="Osiris", type="run", root=True)
async def run_osiris(thread_id: str, human_query: str, file_ids: List[str] = [], update=True,file_sources: Dict[str, list] = None):
    if file_sources is None:
        file_sources = {}

    osiris_agent = await client.beta.assistants.retrieve(assistant_id)

    if update:
        await client.beta.assistants.update(
            assistant_id,
            instructions=osiris_agent.instructions,
            name=osiris_agent.name,
            tools=[{"type": "retrieval"}],
            model="gpt-4-turbo-preview",
            file_ids=file_ids,
        )

    # Add the message to the thread
    init_message = await client.beta.threads.messages.create(
        thread_id=thread_id, role="user", content=human_query
    )

    # Create the run
    run = await client.beta.threads.runs.create(
        thread_id=thread_id, assistant_id=assistant_id
    )

    message_references = {}  # type: Dict[str, cl.Message]
    step_references = {}  # type: Dict[str, cl.Step]
    tool_outputs = []
    # Periodically check for updates

    while True:
        run = await client.beta.threads.runs.retrieve(
            thread_id=thread_id, run_id=run.id
        )

        # Fetch the run steps
        run_steps = await client.beta.threads.runs.steps.list(
            thread_id=thread_id, run_id=run.id, order="asc"
        )

        for step in run_steps.data:
            # Fetch step details
            run_step = await client.beta.threads.runs.steps.retrieve(
                thread_id=thread_id, run_id=run.id, step_id=step.id
            )
            step_details = run_step.step_details
            # Update step content in the Chainlit UI
            if step_details.type == "message_creation":
                thread_message = await client.beta.threads.messages.retrieve(
                    message_id=step_details.message_creation.message_id,
                    thread_id=thread_id,
                )
                await process_thread_message(message_references, thread_message,file_sources)

            if step_details.type == "tool_calls":
                for tool_call in step_details.tool_calls:
                    if isinstance(tool_call, dict):
                        tool_call = DictToObject(tool_call)

                    if tool_call.type == "code_interpreter":
                        await process_tool_call(
                            step_references=step_references,
                            step=step,
                            tool_call=tool_call,
                            name=tool_call.type,
                            input=tool_call.code_interpreter.input
                                  or "# Generating code",
                            output=tool_call.code_interpreter.outputs,
                            show_input="python",
                        )

                        tool_outputs.append(
                            {
                                "output": tool_call.code_interpreter.outputs or "",
                                "tool_call_id": tool_call.id,
                            }
                        )

                    elif tool_call.type == "retrieval":
                        await process_tool_call(
                            step_references=step_references,
                            step=step,
                            tool_call=tool_call,
                            name=tool_call.type,
                            input="Retrieving information",
                            output="Retrieved information",
                        )

                    elif tool_call.type == "function":
                        function_name = tool_call.function.name
                        function_args = json.loads(tool_call.function.arguments)

                        function_output = tool_map[function_name](
                            **json.loads(tool_call.function.arguments)
                        )

                        await process_tool_call(
                            step_references=step_references,
                            step=step,
                            tool_call=tool_call,
                            name=function_name,
                            input=function_args,
                            output=function_output,
                            show_input="json",
                        )

                        tool_outputs.append(
                            {"output": function_output, "tool_call_id": tool_call.id}
                        )
            if (
                    run.status == "requires_action"
                    and run.required_action.type == "submit_tool_outputs"
            ):
                await client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread_id,
                    run_id=run.id,
                    tool_outputs=tool_outputs,
                )

        await cl.sleep(2)  # Refresh every 2 seconds
        if run.status in ["cancelled", "failed", "completed", "expired"]:
            break


# @cl.on_message
# async def on_message(message_from_ui: cl.Message):
#     thread = cl.user_session.get("thread")  # type: Thread
#     es_search_query = await search_documents(user_query=message_from_ui.content)
#     file_paths = await filter_documents(data_dict=es_search_query)
#     files_ids = await upload_files_from_path(file_paths)
#
#     await run(
#         thread_id=thread.id, human_query=message_from_ui.content, file_ids=files_ids
#     )


@cl.on_message
async def main(message_from_ui: cl.Message):
    thread = cl.user_session.get("thread")

    # Initial message to ask the user for their preference
    if not cl.user_session.get("has_made_initial_choice"):
        res = await cl.AskActionMessage(
            content="Do you wish to continue with the RAG approach or the normal assistant?",
            actions=[
                cl.Action(name="rag", value="rag", label="Create a RAG Assistant"),
                cl.Action(name="normal", value="normal", label="Use Normal Assistant"),
            ],
        ).send()

        # Save the user's initial choice to session
        cl.user_session.set("has_made_initial_choice", True)
    else:
        # Follow up based on user's initial choice
        res = await cl.AskActionMessage(
            content="Do you wish to continue with the same method, switch to the other, or try a new RAG?",
            actions=[
                cl.Action(name="continue", value="continue", label="Continue Current Assistant"),
                cl.Action(name="normal", value="normal", label="Use Normal Assistant"),
                cl.Action(name="new_rag", value="new_rag", label="Create a New RAG Assistant"),
            ],
        ).send()

        # Handle user's choice
        if res and res.get("value") == "rag":
            es_search_query = await search_documents(user_query=message_from_ui.content)
            paths = await filter_documents(data_dict=es_search_query)
            files_ids = await upload_files_from_path(paths[0])

            await run_osiris(
                thread_id=thread.id, human_query=message_from_ui.content, file_ids=files_ids,
                file_sources=paths[1]
            )
        elif res and res.get("value") == "normal":
            await run_osiris(
                thread_id=thread.id, human_query=message_from_ui.content
            )
        elif res and res.get("value") == "continue":
            await run_osiris(
                thread_id=thread.id, human_query=message_from_ui.content, update=False)
        elif res and res.get("value") == "new_rag":
            es_search_query = await search_documents(user_query=message_from_ui.content)
            paths = await filter_documents(data_dict=es_search_query)
            files_ids = await upload_files_from_path(paths[0])

            await run_osiris(
                thread_id=thread.id, human_query=message_from_ui.content, file_ids=files_ids,
                file_sources=paths[1]
            )


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    root_messages = [m for m in thread["steps"] if m["parentId"] == None]
    app_user = cl.user_session.get("user")
    await cl.Message(f"Welcome back {app_user.identifier}").send()
