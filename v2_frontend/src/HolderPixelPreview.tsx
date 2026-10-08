import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { fetchSeriesThumbnail, type SeriesPixel } from "./api";

export type PixelPreviewTarget = { pixel: SeriesPixel; anchor: DOMRect };

export default function HolderPixelPreview({ target, pixels }: { target: PixelPreviewTarget | null; pixels: SeriesPixel[] }) {
  const cache = useRef(new Map<string, Blob>());
  const [preview, setPreview] = useState<{ target: PixelPreviewTarget; url: string; message: string } | null>(null);

  useEffect(() => { cache.current.clear(); }, [pixels]);

  useEffect(() => {
    setPreview(null);
    if (!target) return;
    const controller = new AbortController();
    let url = "";
    const timer = window.setTimeout(() => {
      const pixel = target.pixel;
      if (!pixel.last_ivl_file) {
        setPreview({ target, url: "", message: "ВАХ ещё не измерена" });
        return;
      }
      setPreview({ target, url: "", message: "Загружаем график…" });
      const key = JSON.stringify([pixel.pixel_id, pixel.last_ivl_file, pixel.last_updated, pixel.thumbnail_available]);
      const cached = cache.current.get(key);
      void (cached ? Promise.resolve(cached) : fetchSeriesThumbnail(pixel.pixel_id, controller.signal))
        .then((blob) => {
          if (controller.signal.aborted) return;
          // Keep only this series' most recent hover images in memory.
          if (cache.current.size >= 48) cache.current.clear();
          cache.current.set(key, blob);
          url = URL.createObjectURL(blob);
          setPreview({ target, url, message: "" });
        })
        .catch(() => {
          if (!controller.signal.aborted) {
            setPreview({ target, url: "", message: "Не удалось загрузить график. Обновите серию и повторите наведение." });
          }
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [target]);

  if (!preview || preview.target !== target) return null;
  const width = Math.min(380, window.innerWidth - 16);
  const height = 310;
  const { anchor, pixel } = preview.target;
  const left = Math.max(8, Math.min(
    anchor.right + 12 + width <= window.innerWidth - 8 ? anchor.right + 12 : anchor.left - width - 12,
    window.innerWidth - width - 8
  ));
  const top = Math.max(8, Math.min(anchor.top - 24, window.innerHeight - height - 8));

  return createPortal(
    <div className="holder-pixel-preview" id="holder-pixel-preview" role="tooltip" style={{ left, top, width }}>
      <strong>{pixel.pixel_id} · Последняя ВАХ</strong>
      <div className="holder-pixel-preview__graph">
        {preview.url ? <img alt={`ВАХ пикселя ${pixel.pixel_id}`} src={preview.url} /> : <p>{preview.message}</p>}
      </div>
      {preview.url && <div className="holder-pixel-preview__legend"><span>Ток OLED / LED</span><span>Фототок</span></div>}
    </div>,
    document.body
  );
}
