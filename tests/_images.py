import base64

from harnessed.core.messages import ImageDetail, ImagePart


def inline(data: bytes, detail: ImageDetail = ImageDetail.AUTO) -> ImagePart:
    return ImagePart("data:image/png;base64," + base64.b64encode(data).decode("ascii"), detail)
