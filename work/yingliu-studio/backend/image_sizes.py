import re
from math import gcd, isqrt


IMAGE_RATIOS = ('1:1', '3:2', '2:3', '4:3', '3:4', '16:9', '9:16', '21:9')
IMAGE_RESOLUTIONS = {'1K': 1024, '2K': 2048, '3K': 3072, '4K': 4096}
MAX_IMAGE_EDGE = 8192
MAX_IMAGE_PIXELS = 24 * 1024 * 1024


def parse_image_size(size):
    """Read a bounded pixel size from a canvas or generation request."""
    if not isinstance(size, str):
        return None
    match = re.fullmatch(r'\s*([0-9]{1,4})\s*[xX×]\s*([0-9]{1,4})\s*', size)
    if not match:
        return None
    width, height = map(int, match.groups())
    if 0 < min(width, height) and max(width, height) <= MAX_IMAGE_EDGE and width * height <= MAX_IMAGE_PIXELS:
        return width, height
    return None


def image_size_ratio(width, height):
    factor = gcd(width, height)
    exact_ratio = f'{width // factor}:{height // factor}'
    if exact_ratio in IMAGE_RATIOS:
        return exact_ratio
    # Canvas rounds pixel dimensions independently for some standard ratios.
    for ratio in IMAGE_RATIOS:
        horizontal, vertical = map(int, ratio.split(':'))
        if 100 * abs(width * vertical - height * horizontal) <= height * horizontal:
            return ratio
    return exact_ratio


def image_resolution_for_size(width, height):
    long_edge = max(width, height)
    area = width * height
    if long_edge >= 3840 or area >= 7 * 1024 * 1024:
        return '4K'
    if long_edge >= 2048 or area >= 2 * 1024 * 1024:
        return '2K'
    return '1K'


def image_dimensions(ratio, resolution, size=None):
    """Use explicit pixels or fit the selected ratio to the resolution's long edge."""
    dimensions = parse_image_size(size)
    if dimensions:
        return dimensions
    if ratio not in IMAGE_RATIOS:
        ratio = '1:1'
    ratio_width, ratio_height = map(int, ratio.split(':'))
    factor = gcd(ratio_width, ratio_height)
    ratio_width //= factor
    ratio_height //= factor
    long_edge = IMAGE_RESOLUTIONS.get(resolution, IMAGE_RESOLUTIONS['1K'])
    # Keep whole pixels and align both edges to 16 without changing the aspect ratio.
    scale = 16 * (long_edge // (16 * max(ratio_width, ratio_height)))
    return ratio_width * scale, ratio_height * scale


def gpt_image_2_dimensions(width, height):
    """Scale aligned dimensions to the model's edge and pixel limits."""
    requested_scale = gcd(width, height)
    width_unit = 16 * (width // requested_scale)
    height_unit = 16 * (height // requested_scale)
    unit_pixels = width_unit * height_unit
    min_scale = isqrt((655_360 - 1) // unit_pixels) + 1
    max_scale = min(3840 // max(width_unit, height_unit), isqrt(8_294_400 // unit_pixels))
    scale = min(max_scale, max(min_scale, requested_scale // 16))
    return width_unit * scale, height_unit * scale
