"""Brand images for the GitHub page from assets/shadow_1024.png (PIL only, deterministic).
Usage: python tools/make_brand_images.py   -> docs/banner.png (1600x500, README top) + docs/social_preview.png (1280x640,
GitHub Settings > Social preview: the picture shown when the repo link is shared)."""
import os
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG, ORANGE, TXT, SUB = (11, 10, 24), (255, 122, 48), (245, 245, 250), (196, 190, 214)
F = lambda w, s: ImageFont.truetype(os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', {'b': 'segoeuib.ttf', 'r': 'segoeui.ttf', 's': 'seguisb.ttf'}[w]), s)


def canvas(W, H):
    im = Image.new('RGB', (W, H), BG)
    glow = Image.new('RGB', (W, H), (0, 0, 0)); d = ImageDraw.Draw(glow)
    d.ellipse((-W * 0.15, -H * 0.6, W * 0.55, H * 1.6), fill=(70, 28, 10))     # warm light behind the logo
    d.ellipse((W * 0.65, H * 0.3, W * 1.3, H * 1.8), fill=(24, 16, 52))         # cool corner
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(H * 0.35)), 0.9)
    return im


def chip(d, x, y, text, font):
    w = d.textlength(text, font=font) + 34; h = font.size + 22
    d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=(30, 26, 52), outline=(90, 70, 120), width=2)
    d.text((x + 17, y + h / 2), text, font=font, fill=SUB, anchor='lm'); return x + w + 14


def main():
    logo = Image.open(os.path.join(ROOT, 'assets', 'shadow_1024.png')).convert('RGBA')
    # banner 1600x500
    W, H = 1600, 500; im = canvas(W, H); L = logo.resize((360, 360), Image.LANCZOS); im.paste(L, (90, 70), L); d = ImageDraw.Draw(im)
    d.text((510, 150), 'Seekframe', font=F('b', 120), fill=TXT, anchor='lm')
    d.text((514, 250), 'HTML & Blender scenes  \u2192  MP4 for YouTube and Shorts', font=F('s', 40), fill=ORANGE, anchor='lm')
    x = 514
    for t in ('4K \u00b7 30 fps \u00b7 sound', '16:9 + 9:16', 'AI: idea \u2192 video', '11 languages', 'free'):
        x = chip(d, x, 318, t, F('r', 24))
    im.save(os.path.join(ROOT, 'docs', 'banner.png'), optimize=True)
    # social preview 1280x640
    W, H = 1280, 640; im = canvas(W, H); L = logo.resize((300, 300), Image.LANCZOS); im.paste(L, (80, 110), L); d = ImageDraw.Draw(im)
    d.text((430, 200), 'Seekframe', font=F('b', 120), fill=TXT, anchor='lm')
    d.text((434, 300), 'HTML & Blender scenes \u2192 MP4', font=F('s', 44), fill=ORANGE, anchor='lm')
    d.text((434, 360), 'for YouTube videos and Shorts', font=F('r', 34), fill=SUB, anchor='lm')
    x = 434
    for t in ('4K · 30 fps', '16:9 + 9:16', 'AI', '11 languages', 'QC'):
        x = chip(d, x, 420, t, F('r', 24))
    d.text((80, 580), 'Free Windows app  \u00b7  github.com/Reemon88IQ/seekframe', font=F('r', 26), fill=(150, 144, 170), anchor='lm')
    im.save(os.path.join(ROOT, 'docs', 'social_preview.png'), optimize=True)
    print('wrote docs/banner.png and docs/social_preview.png')


if __name__ == '__main__':
    main()
