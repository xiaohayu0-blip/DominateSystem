"""Run a locally installed RapidOCR engine in its own Python environment."""

import json
import site
import sys


def main():
    site_packages = sys.argv[1]
    if site_packages:
        site.addsitedir(site_packages)

    import numpy as np
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
    texts = []
    for image_path in json.load(sys.stdin):
        with Image.open(image_path) as image:
            image.thumbnail((2200, 2200))
            pixels = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
        result, _ = engine(pixels)
        texts.append("\n".join(str(line[1]).strip() for line in result or []))
    json.dump(texts, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
