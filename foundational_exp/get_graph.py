import os
import json
import asyncio
import pickle
from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_openai import ChatOpenAI

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openrouter_client import OPENROUTER_BASE_URL, get_model, get_max_tokens

llm = ChatOpenAI(temperature=0, max_tokens=get_max_tokens(), model_name=get_model(), openai_api_key=os.environ["OPENROUTER_API_KEY"], openai_api_base=OPENROUTER_BASE_URL)
llm_transformer = LLMGraphTransformer(llm=llm)

async def main():
    with open('AgentJudge-strict-raw.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    documents = [Document(page_content=item['raw_record'], metadata={**item}) for item in data if item['ambiguous'] == 0]

    batch_size = 10
    graph_documents = []
    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]
        graph_documents_batch = await llm_transformer.aconvert_to_graph_documents(batch)
        if i == 0:
            graph_documents = graph_documents_batch
        else:
            graph_documents.extend(graph_documents_batch)

    output_filename = 'graph_documents.pkl'
    with open(output_filename, 'wb') as f: 
        pickle.dump(graph_documents, f)

if __name__ == "__main__":
    asyncio.run(main())
