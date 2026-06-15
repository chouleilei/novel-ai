import io
import uuid
import zipfile
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Chapter, Project


class ExportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def export_markdown(self, project_id: uuid.UUID) -> str:
        project, chapters = await self._get_project_and_chapters(project_id)
        parts = [f"# {project.title}", ""]
        for chapter in chapters:
            parts.append(f"## 第{chapter.chapter_number}章")
            parts.append(chapter.final_content or "_尚未生成_")
            parts.append("")
        return "\n".join(parts)

    async def export_txt(self, project_id: uuid.UUID) -> str:
        project, chapters = await self._get_project_and_chapters(project_id)
        parts = [project.title, "=" * len(project.title), ""]
        for chapter in chapters:
            parts.append(f"第{chapter.chapter_number}章")
            parts.append("")
            parts.append(chapter.final_content or "尚未生成")
            parts.append("")
        return "\n".join(parts)

    async def export_epub(self, project_id: uuid.UUID) -> bytes:
        project, chapters = await self._get_project_and_chapters(project_id)
        archive = io.BytesIO()
        safe_title = self._safe_identifier(project.title)
        with zipfile.ZipFile(archive, "w") as epub:
            epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            epub.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
            )
            chapter_items = []
            manifest_items = [
                '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
            ]
            spine_items = []

            for chapter in chapters:
                item_id = f"chapter-{chapter.chapter_number}"
                filename = f"{item_id}.xhtml"
                chapter_items.append((chapter.chapter_number, filename))
                manifest_items.append(f'<item id="{item_id}" href="{filename}" media-type="application/xhtml+xml"/>')
                spine_items.append(f'<itemref idref="{item_id}"/>')
                epub.writestr(
                    f"OEBPS/{filename}",
                    self._render_epub_chapter(project.title, chapter.chapter_number, chapter.final_content or "尚未生成"),
                )

            epub.writestr("OEBPS/nav.xhtml", self._render_nav(project.title, chapter_items))
            epub.writestr(
                "OEBPS/content.opf",
                self._render_content_opf(project.title, safe_title, manifest_items, spine_items),
            )
        return archive.getvalue()

    async def _get_project_and_chapters(self, project_id: uuid.UUID) -> tuple[Project, list[Chapter]]:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise ValueError("项目不存在")
        stmt = (
            select(Chapter)
            .where(Chapter.project_id == project_id)
            .order_by(Chapter.chapter_number.asc())
        )
        result = await self.session.execute(stmt)
        return project, list(result.scalars())

    def _render_epub_chapter(self, title: str, chapter_number: int, content: str) -> str:
        escaped_title = escape(title)
        escaped_content = "<br/>".join(escape(line) for line in content.splitlines()) or "尚未生成"
        return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">
  <head>
    <title>{escaped_title} - 第{chapter_number}章</title>
  </head>
  <body>
    <h1>第{chapter_number}章</h1>
    <p>{escaped_content}</p>
  </body>
</html>"""

    def _render_nav(self, title: str, chapter_items: list[tuple[int, str]]) -> str:
        links = "\n".join(
            f'      <li><a href="{filename}">第{chapter_number}章</a></li>'
            for chapter_number, filename in chapter_items
        )
        return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">
  <head>
    <title>{escape(title)}</title>
  </head>
  <body>
    <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
      <h1>{escape(title)}</h1>
      <ol>
{links}
      </ol>
    </nav>
  </body>
</html>"""

    def _render_content_opf(
        self,
        title: str,
        identifier: str,
        manifest_items: list[str],
        spine_items: list[str],
    ) -> str:
        manifest = "\n    ".join(manifest_items)
        spine = "\n    ".join(spine_items)
        return f"""<?xml version="1.0" encoding="utf-8"?>
<package version="3.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{escape(identifier)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    {manifest}
  </manifest>
  <spine>
    {spine}
  </spine>
</package>"""

    def _safe_identifier(self, title: str) -> str:
        normalized = "".join(ch for ch in title if ch.isalnum())[:32]
        return normalized or "novel"
