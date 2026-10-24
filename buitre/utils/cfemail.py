def decode_cfemail(encoded: str) -> str | None:
    """
    Decode Cloudflare "Email Address Obfuscation" (data-cfemail=...).

    The first byte is an XOR key applied to every following byte. Very
    common on contact pages: the address never appears in the markup, so
    a plain regex finds nothing.
    """

    encoded = (encoded or "").strip()

    if len(encoded) < 4 or len(encoded) % 2:
        return None

    try:
        key = int(encoded[:2], 16)
        raw = bytes(
            int(encoded[i:i + 2], 16) ^ key
            for i in range(2, len(encoded), 2)
        )

        return raw.decode("utf-8")

    except ValueError:
        return None
