from .base import Reader, Writer
from .epub import EpubReader, EpubWriter
from .image import ImageReader
from .lines import LineWriter
from .mobi import MobiReader
from .pdf import PdfReader, PdfWriter
from .txt import TxtReader, TxtWriter

READERS = {"txt": TxtReader, "epub": EpubReader, "pdf": PdfReader,
           "mobi": MobiReader, "azw3": MobiReader,
           "jpg": ImageReader, "jpeg": ImageReader, "png": ImageReader,
           "webp": ImageReader, "tiff": ImageReader, "bmp": ImageReader}
WRITERS = {"txt": TxtWriter, "epub": EpubWriter, "pdf": PdfWriter}

__all__ = [
    "Reader", "Writer", "READERS", "WRITERS",
    "TxtReader", "TxtWriter", "EpubReader", "EpubWriter",
    "PdfReader", "PdfWriter", "LineWriter", "MobiReader", "ImageReader",
]
