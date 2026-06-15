import asyncio
import re
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from backend.db.models.chapter import ChapterOutline
from backend.services.project_service import ProjectService
import uuid

DATABASE_URL = "postgresql+asyncpg://postgres:postgres@db:5432/novel_ai"

engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def main():
    project_id = uuid.UUID("deb1b446-1fef-4489-b664-edd31da7ce9c")
    
    async with AsyncSessionLocal() as session:
        stmt = select(ChapterOutline).where(ChapterOutline.project_id == project_id).order_by(ChapterOutline.chapter_number.asc())
        result = await session.execute(stmt)
        outlines = list(result.scalars())
        
        full_text = ""
        for o in outlines:
            text = o.outline_text.strip()
            full_text += text + "\n\n"
            
        print("Finding missing chapters 3 and 6 by looking for their content...")
        
        # We know chapter 3 is missing, so we look for "第3章"
        # We know chapter 6 is missing, so we look for "第6章"
        
        # Let's try a very liberal regex
        pattern = re.compile(r'(?:\*{1,2}\s*)?(?:[#＃]\s*)?(?:[【\[(（]\s*)?第\s*[0-9零一二三四五六七八九十百千两]+\s*(?:章|回|节|幕).*?(?=\n|$)', re.MULTILINE)
        
        # Check if the text actually CONTAINS chapter 3 or 6 anywhere at all
        if "第3章" not in full_text:
            print("WARNING: The string '第3章' does not exist in the database text AT ALL.")
        else:
            print("FOUND '第3章' in text. Context:")
            idx = full_text.find("第3章")
            print(full_text[max(0, idx-50):min(len(full_text), idx+100)])
            
        if "第6章" not in full_text:
            print("WARNING: The string '第6章' does not exist in the database text AT ALL.")
        else:
            print("FOUND '第6章' in text. Context:")
            idx = full_text.find("第6章")
            print(full_text[max(0, idx-50):min(len(full_text), idx+100)])

if __name__ == "__main__":
    asyncio.run(main())
