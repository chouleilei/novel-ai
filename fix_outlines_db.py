import asyncio
import re
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from backend.db.models.chapter import ChapterOutline
from backend.services.project_service import ProjectService
import uuid

# Use docker network name instead of localhost inside the container
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
            
        print("Total length of combined text:", len(full_text))
        
        # New pattern that mirrors the improved one
        pattern = re.compile(r'^(?:\*{1,2}\s*)?(?:[#＃]\s*)?(?:[【\[(（]\s*)?第\s*[0-9零一二三四五六七八九十百千两]+\s*(?:章|回|节|幕).*?(?=\n|$)', re.MULTILINE)
        
        matches = list(pattern.finditer(full_text))
        print("Found headings count:", len(matches))
        
        if len(matches) > 0:
            new_outlines = []
            for i in range(len(matches)):
                start = matches[i].start()
                end = matches[i+1].start() if i + 1 < len(matches) else len(full_text)
                block = full_text[start:end].strip()
                if block:
                    new_outlines.append({
                        "chapter_number": i + 1,
                        "outline_text": block
                    })
            
            print("Parsed new outlines count:", len(new_outlines))
            
            if len(new_outlines) == 100:
                print("Successfully parsed 100 chapters. Importing...")
                service = ProjectService(session)
                await service.import_outlines(project_id, new_outlines)
                await session.commit()
                print("Import complete.")
            else:
                print("Still didn't get exactly 100 chapters. Dumping missing sequences...")
                for i in range(len(new_outlines)):
                    print(f"{i+1}: {new_outlines[i]['outline_text'].split(chr(10))[0]}")
        else:
            print("No headings found.")

if __name__ == "__main__":
    asyncio.run(main())
