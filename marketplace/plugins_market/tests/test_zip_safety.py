import io
import stat
import zipfile

import pytest

from plugins_market.core.errors import PublishError
from plugins_market.validation.zip_utils import validate_zip_safety


def test_zip_safety_rejects_symlink_when_unix_permissions_are_present():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as zf:
        info = zipfile.ZipInfo("outer/payload/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "target")

    with zipfile.ZipFile(io.BytesIO(content.getvalue())) as zf:
        with pytest.raises(PublishError, match="符号链接"):
            validate_zip_safety(zf)
