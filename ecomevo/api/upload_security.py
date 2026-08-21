from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


RASTER_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff"}
DOC_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm"}
BLOCKED_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".sh", ".com", ".scr", ".msi", ".apk", ".jar"}
TEXT_SUFFIXES = {".txt", ".log", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".md"}
PUBLIC_ASSET_META = {"kind", "width", "height", "mode", "duration", "bit_rate", "sample_rate", "channels", "pages", "indexed_pages", "rows", "indexed_rows", "sheets", "paragraphs", "tables", "chars", "lines", "sha256", "semantic_observation_count", "semantic_confidence"}


def normalize_upload_type(filename: str, mime: str) -> tuple[str, str]:
    suffix = Path(filename or "").suffix.lower()
    guessed = mimetypes.guess_type(filename or "")[0]
    if suffix in BLOCKED_SUFFIXES:
        raise HTTPException(415, "不支持可执行文件或脚本文件")
    if guessed and guessed.startswith(("image/", "video/", "audio/")):
        mime = guessed
    elif suffix == ".pdf":
        mime = "application/pdf"
    elif suffix == ".docx":
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif suffix in {".xlsx", ".xlsm"}:
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix in TEXT_SUFFIXES and (not mime or mime == "application/octet-stream"):
        mime = guessed or "text/plain"
    elif guessed and mime in {"", "application/octet-stream"}:
        mime = guessed
    mime = mime or "application/octet-stream"
    allowed = mime.startswith(("image/", "video/", "audio/", "text/")) or suffix in DOC_SUFFIXES | TEXT_SUFFIXES or mime in {"application/pdf", "application/json", "application/xml"}
    if not allowed:
        raise HTTPException(415, "暂不支持该文件类型")
    return suffix, mime[:120]


def upload_limit(mime: str, suffix: str) -> int:
    if mime.startswith("image/"):
        return 25 * 1024 * 1024
    if mime.startswith(("audio/", "video/")):
        return 150 * 1024 * 1024
    if suffix in DOC_SUFFIXES or mime == "application/pdf":
        return 80 * 1024 * 1024
    return 25 * 1024 * 1024


def validate_raster(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width > 20000 or height > 20000 or width * height > 80_000_000:
                raise HTTPException(413, "图片尺寸过大")
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, "图片像素尺寸过大") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(400, "图片文件损坏或格式不正确") from exc


def validate_office_zip(path: Path, suffix: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 10000:
                raise HTTPException(413, "文档内部文件数量过多")
            total = sum(max(0, info.file_size) for info in infos)
            compressed = sum(max(1, info.compress_size) for info in infos)
            if total > 500 * 1024 * 1024 or total / max(1, compressed) > 150:
                raise HTTPException(413, "文档解压体积异常")
            names = {info.filename for info in infos}
            if suffix == ".docx" and not {"[Content_Types].xml", "word/document.xml"} <= names:
                raise HTTPException(400, "Word 文件结构不完整")
            if suffix in {".xlsx", ".xlsm"} and not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                raise HTTPException(400, "Excel 文件结构不完整")
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise HTTPException(400, "Office 文件损坏或格式不正确") from exc


def validate_pdf(path: Path) -> None:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise HTTPException(400, "暂不支持加密 PDF")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, "暂不支持加密 PDF") from exc
        _ = len(reader.pages)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "PDF 文件损坏或格式不正确") from exc


def validate_av_media(path: Path, mime: str) -> None:
    kind = "video" if mime.startswith("video/") else "audio"
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(400, "媒体文件无法读取") from exc
    if result.returncode != 0:
        raise HTTPException(400, "媒体文件损坏或格式不正确")
    try:
        streams = json.loads(result.stdout or "{}").get("streams", [])
    except Exception:
        streams = []
    if not any(row.get("codec_type") == kind for row in streams if isinstance(row, dict)):
        raise HTTPException(400, "媒体文件内容与声明类型不匹配")


def validate_uploaded_file(path: Path, mime: str, suffix: str) -> None:
    if mime.startswith("image/"):
        validate_raster(path)
    elif mime.startswith(("audio/", "video/")):
        validate_av_media(path, mime)
    elif suffix == ".pdf" or mime == "application/pdf":
        validate_pdf(path)
    elif suffix in {".docx", ".xlsx", ".xlsm"}:
        validate_office_zip(path, suffix)


def safe_download_name(value: str) -> str:
    value = Path(value or "download").name
    value = re.sub(r"[\x00-\x1f\x7f]+", "", value).strip()
    return value[:180] or "download"


def public_asset(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("meta") or {}
    public = {key: value for key, value in row.items() if key not in {"path", "meta"}}
    public["meta"] = {key: meta[key] for key in PUBLIC_ASSET_META if key in meta}
    public["url"] = f"/api/assets/{row['id']}/file"
    if str(row.get("mime", "")).startswith(("image/", "video/")):
        public["preview_url"] = f"/api/assets/{row['id']}/preview/0"
    return public
