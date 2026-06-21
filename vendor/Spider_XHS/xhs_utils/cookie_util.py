INVISIBLE_PASTE_CHARS = {
    "\ufeff": "",
    "\u200b": "",
    "\u200c": "",
    "\u200d": "",
    "\u2060": "",
    "\xa0": " ",
}


def clean_pasted_text(value):
    text = str(value or "")
    for source, target in INVISIBLE_PASTE_CHARS.items():
        text = text.replace(source, target)
    return text.strip()


def trans_cookies(cookies_str):
    cookies_str = clean_pasted_text(cookies_str)
    if '; ' in cookies_str:
        ck = {clean_pasted_text(i.split('=')[0]): clean_pasted_text('='.join(i.split('=')[1:])) for i in cookies_str.split('; ')}
    else:
        ck = {clean_pasted_text(i.split('=')[0]): clean_pasted_text('='.join(i.split('=')[1:])) for i in cookies_str.split(';')}
    return ck
