import chromadb
from src.config import settings

client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
for c in client.list_collections():
    name = c if isinstance(c, str) else c.name
    print(repr(name))