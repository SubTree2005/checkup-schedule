"""Bound inline image cost in patient JSON; originals remain in the database."""
import base64
import binascii
import warnings
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


@lru_cache(maxsize=16)
def patient_image(value: str | None) -> str | None:
    if not value or not value.startswith('data:image/'):
        return value
    if len(value) > 1_500_000:
        return None
    try:
        raw = base64.b64decode(value.split(',', 1)[1], validate=True)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.width * source.height > 16_000_000:
                    return None
                source.thumbnail((256, 256))
                picture = ImageOps.exif_transpose(source).convert('RGB')
                for size in (256, 160, 96, 48):
                    picture.thumbnail((size, size))
                    output = BytesIO()
                    picture.save(output, 'JPEG', quality=60, optimize=True)
                    encoded = base64.b64encode(output.getvalue()).decode('ascii')
                    if len(encoded) <= 12_000:
                        return 'data:image/jpeg;base64,' + encoded
    except (ValueError, IndexError, OSError, binascii.Error, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        pass
    return None
