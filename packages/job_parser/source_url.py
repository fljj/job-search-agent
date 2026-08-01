from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SOURCE_DOMAINS = {
    "BOSS": ("zhipin.com",),
    "LIEPIN": ("liepin.com",),
    "MAIMAI": ("maimai.cn",),
}
_TRACKING_PARAMETERS = {"from", "source", "track", "time"}


def normalize_job_source_url(source: str, value: str | None) -> str | None:
    """校验招聘平台域名并移除不参与职位定位的跟踪参数。"""
    if not value:
        return None
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    allowed_domains = _SOURCE_DOMAINS.get(source.upper(), ())
    if parsed.scheme not in {"http", "https"} or not any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in allowed_domains
    ):
        raise ValueError("职位来源链接与招聘平台不匹配")
    query = urlencode([
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMETERS
        and not key.lower().startswith("utm_")
    ])
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, query, ""))
