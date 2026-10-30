"""重新生成多尺寸图标，并通知资源管理器刷新图标缓存。"""

import ctypes
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "app.ico")
BASE = 256

img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([10, 10, BASE - 10, BASE - 10], radius=58,
                    fill=(52, 211, 153, 255))
d.rounded_rectangle([10, 10, BASE - 10, BASE - 10], radius=58,
                    outline=(20, 160, 110, 255), width=4)

font = None
for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\seguisb.ttf"):
    try:
        font = ImageFont.truetype(p, 168)
        break
    except Exception:
        continue
if font:
    d.text((BASE // 2, BASE // 2 - 8), "¥", font=font, fill=(18, 21, 26, 255),
           anchor="mm")

img.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                     (64, 64), (128, 128), (256, 256)])

chk = Image.open(ICO)
print("ico written:", os.path.getsize(ICO), "bytes, sizes:",
      sorted(chk.ico.sizes()))

# 通知资源管理器重新读取图标
SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000
ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
print("icon cache notified")
