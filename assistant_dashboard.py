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
from code_editor import code_editor

load_dotenv()

# Initialize Elasticsearch client
index_name = "ikms-assistants"

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
    if assistant_id != 'New':  # Update existing assistant
        es.update(index=index_name, id=assistant_id, doc=document_body)
        st.success(f"Assistant {assistant_name} updated successfully!")
    else:  # Create new assistant
        es.index(index=index_name, document=document_body)
        st.success("Assistant created successfully!")


def delete_assistant(a_id,assistant_id):
    """Delete an assistant from Elasticsearch."""
    es.delete(index=index_name, id=a_id)
    response = client.beta.assistants.delete(assistant_id)
    st.write(response)
    st.success("Assistant deleted successfully.")


def assistant_form(a_id, assistants, category):
    """Form for adding or updating assistants."""

    if a_id != 'New':
        assistant = next((a for a in assistants if a['id'] == a_id), None)
        if assistant:
            assistant_name = assistant['assistant_name']
            assistant_id = assistant['assistant_id']
            prompt = assistant['Prompt']
            document_body = {k: v for k, v in assistant.items() if k not in ['id', 'assistant_name']}
        else:
            st.error("Selected assistant not found.")
            return

    else:
        assistant_name = ""
        prompt = ""
        assistant_id=""
        document_body = {}

    assistant_name = st.text_input("Assistant Name", value=assistant_name, key=category + str(2))

    with open('code-editor-config/custom_buttons_bar_alt.json') as json_button_file_alt:
        custom_buttons_alt = json.load(json_button_file_alt)

    # Load Info bar CSS from JSON file
    with open('code-editor-config/info_bar.json') as json_info_file:
        info_bar = json.load(json_info_file)

    # Load Code Editor CSS from file
    with open('code-editor-config/code_editor_css.scss') as css_file:
        css_text = css_file.read()

    # construct component props dictionary (->Code Editor)
    comp_props = {"css": css_text, "globalCSS": ":root {\n  --streamlit-dark-font-family: monospace;\n}"}

    mode_list = ["abap", "abc", "actionscript", "ada", "alda", "apache_conf", "apex", "applescript", "aql", "asciidoc",
                 "asl", "assembly_x86", "autohotkey", "batchfile", "bibtex", "c9search", "c_cpp", "cirru", "clojure",
                 "cobol", "coffee", "coldfusion", "crystal", "csharp", "csound_document", "csound_orchestra",
                 "csound_score", "csp", "css", "curly", "d", "dart", "diff", "django", "dockerfile", "dot", "drools",
                 "edifact", "eiffel", "ejs", "elixir", "elm", "erlang", "forth", "fortran", "fsharp", "fsl", "ftl",
                 "gcode", "gherkin", "gitignore", "glsl", "gobstones", "golang", "graphqlschema", "groovy", "haml",
                 "handlebars", "haskell", "haskell_cabal", "haxe", "hjson", "html", "html_elixir", "html_ruby", "ini",
                 "io", "ion", "jack", "jade", "java", "javascript", "jexl", "json", "json5", "jsoniq", "jsp", "jssm",
                 "jsx", "julia", "kotlin", "latex", "latte", "less", "liquid", "lisp", "livescript", "logiql",
                 "logtalk", "lsl", "lua", "luapage", "lucene", "makefile", "markdown", "mask", "matlab", "maze",
                 "mediawiki", "mel", "mips", "mixal", "mushcode", "mysql", "nginx", "nim", "nix", "nsis", "nunjucks",
                 "objectivec", "ocaml", "partiql", "pascal", "perl", "pgsql", "php", "php_laravel_blade", "pig",
                 "plain_text", "powershell", "praat", "prisma", "prolog", "properties", "protobuf", "puppet", "python",
                 "qml", "r", "raku", "razor", "rdoc", "red", "redshift", "rhtml", "robot", "rst", "ruby", "rust", "sac",
                 "sass", "scad", "scala", "scheme", "scrypt", "scss", "sh", "sjs", "slim", "smarty", "smithy",
                 "snippets", "soy_template", "space", "sparql", "sql", "sqlserver", "stylus", "svg", "swift", "tcl",
                 "terraform", "tex", "text", "textile", "toml", "tsx", "turtle", "twig", "typescript", "vala",
                 "vbscript", "velocity", "verilog", "vhdl", "visualforce", "wollok", "xml", "xquery", "yaml", "zeek"]

    height = [19, 22]
    language = "python"
    theme = "default"
    shortcuts = "vscode"
    focus = False
    wrap = True
    btns = custom_buttons_alt
    ace_props = {"style": {"borderRadius": "0px 0px 8px 8px"}}

    response_dict = code_editor(prompt, height=height, lang=language, theme=theme,
                                shortcuts=shortcuts,
                                focus=focus, buttons=btns, info=info_bar, props=ace_props, options={"wrap": wrap},
                                allow_reset=True, key=category + str(3))

    if len(response_dict['id']) != 0 and (response_dict['type'] == "submit" or response_dict['type'] == "selection"):
        st.write(response_dict, key=category + str(4))

    assistant = None

    if a_id == 'New':
        assistant = client.beta.assistants.create(
            name=assistant_name,
            instructions=response_dict['text'],
            tools=[{"type": "retrieval"}],
            model="gpt-4-turbo-preview",
        )
    else:
        assistant = client.beta.assistants.retrieve(assistant_id)
        assistant = client.beta.assistants.update(
            assistant_id,
            instructions=response_dict['text'],
            name=assistant_name,
            tools=[{"type": "retrieval"}],
            model="gpt-4-turbo-preview", )

    document = {
        "assistant_id": assistant.id,
        "Prompt": response_dict['text'],
        "assistant_name": assistant_name
    }

    if st.button("Save Assistant", key=category + str(5)):
        if assistant_name:
            try:
                create_or_update_assistant(a_id, assistant_name, document)
            except SyntaxError:
                st.error("Invalid format. Please enter a valid Python dictionary.")


def main():
    st.title("Assistant Management Dashboard")

    if st.button("Refresh Assistant List"):
        st.experimental_rerun()

    assistants = fetch_all_assistants()
    assistant_df = pd.DataFrame(assistants)

    if not assistant_df.empty:

        with st.expander("Assistants", expanded=True):
            assistant_df_display = assistant_df.drop(columns=['Prompt'], errors='ignore')
            st.write("Assistants:")
            st.table(assistant_df_display)

        name_to_id = pd.Series(assistant_df.id.values, index=assistant_df.assistant_name).to_dict()
        id_to_assistant = pd.Series(assistant_df.assistant_id.values, index=assistant_df.id).to_dict()
        options = [''] + list(assistant_df['assistant_name'])

        with st.expander("Delete Assistants", expanded=True):
            # Use the assistant names as the selectbox options
            selected_name = st.selectbox("Select an assistant to delete", options=options)
            selected_id = name_to_id.get(selected_name, '')
            assistant_id = id_to_assistant.get(selected_id, '')

            if selected_id:
                if st.button("Delete Assistant"):
                    delete_assistant(selected_id, assistant_id)
                    st.experimental_rerun()

        with st.expander("Create Assistant", expanded=True):
            a_id = 'New'
            assistant_form(a_id, assistants, 'CreateAssistant')

        with st.expander("Update Assistant", expanded=True):
            selected_name = st.selectbox("Choose the assistant that need to edit", options=options)
            selected_id = name_to_id.get(selected_name, '')
            assistant_form(selected_id, assistants, 'UpdateAssistant')
    else:
        st.write("Currently No Assistants are Available")

        with st.expander("Create Assistant", expanded=True):
            a_id = 'New'
            assistant_form(a_id, assistants, 'CreateAssistant')


if __name__ == "__main__":
    main()
