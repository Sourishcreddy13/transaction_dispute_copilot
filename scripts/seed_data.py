from __future__ import annotations
import os
from app.core.settings import Settings
from app.core.db import DB
from app.core.rag import PolicyRAG
from app.workflow import Copilot

def main():
 s=Settings(); DB(s.db_path)
 rag=PolicyRAG(s.rag_path,s.embedding_model,s.rag_mode); print('Indexed policy documents:',rag.index_directory())
 cop=Copilot(settings=s); cid=cop.db.create_case('C-1001','analyst:A-001'); print('Demo case:',cid)
 print('Seed complete')
if __name__=='__main__':main()
