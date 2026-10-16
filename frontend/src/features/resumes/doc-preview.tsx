"use client";
import * as React from "react";
import type { DocPreview } from "@/types/api";

/**
 * Renders a server-generated preview. DOCX previews are HTML produced by the backend from the user's own
 * document with every text node escaped server-side; PDF previews are PNG data URLs.
 */
export function DocumentPreview({ preview }: { preview: DocPreview }) {
  const box = React.useRef<HTMLDivElement>(null);
  const [scale, setScale] = React.useState(1);
  React.useLayoutEffect(() => {
    if (preview.format !== "docx" || !box.current) return;
    const fit = () => {
      const page = box.current?.querySelector<HTMLElement>(".cc-docx-page");
      if (!page || !box.current) return;
      setScale(Math.min(1, (box.current.clientWidth - 8) / page.scrollWidth));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [preview]);

  return (
    <div className="space-y-2">
      <p className="text-center text-xs text-muted-foreground">{preview.fidelity}</p>
      <div ref={box} className="overflow-hidden rounded-lg bg-muted p-2 sm:p-4">
        {preview.format === "pdf" ? (
          preview.pages.map((src, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={i} src={src} alt={`Page ${i + 1}`} className="mx-auto mb-4 block max-w-full bg-white shadow" />
          ))
        ) : (
          <div style={{ transform: `scale(${scale})`, transformOrigin: "top left", width: scale < 1 ? `${100 / scale}%` : undefined }}
            className="docx-preview" dangerouslySetInnerHTML={{ __html: preview.html }} />
        )}
      </div>
    </div>
  );
}
