"""Hỗ trợ gọi HTTPS ổn định trên các máy Windows.

Một số máy cài Python "mỏng" không kèm chứng chỉ CA gốc nên mọi request HTTPS
đều bị lỗi:
    SSL: CERTIFICATE_VERIFY_FAILED / SSL certificate verify failed

Hàm `urlopen` ở đây sẽ:
    1. Thử mở URL bằng chứng chỉ hệ thống.
    2. Nếu lỗi xác thực SSL -> thử lại bằng SSL context không xác thực chứng chỉ
       (chỉ nên dùng cho app nội bộ / GitHub release, không dùng cho giao dịch
       nhạy cảm cần bảo mật nghiêm ngặt).
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request


def _verified_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _unverified_context() -> ssl.SSLContext:
    return ssl._create_unverified_context()


def urlopen(request, timeout=None):
    """Mở request; tự hạ cấp SSL verification nếu máy thiếu CA certificate."""
    try:
        return urllib.request.urlopen(
            request,
            timeout=timeout,
            context=_verified_context(),
        )
    except (urllib.error.URLError, ssl.SSLError) as exc:
        reason = getattr(exc, "reason", None)
        message = str(reason or exc)
        text = message.upper()

        # Chỉ thử lại khi đúng là lỗi chứng chỉ SSL, không nuốt lỗi HTTP/network.
        is_ssl_error = isinstance(exc, ssl.SSLError) or isinstance(
            reason, ssl.SSLError
        )
        if not is_ssl_error and "CERTIFICATE_VERIFY_FAILED" not in text and "SSL" not in text:
            raise

        return urllib.request.urlopen(
            request,
            timeout=timeout,
            context=_unverified_context(),
        )
