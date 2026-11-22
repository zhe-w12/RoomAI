import { useEffect, useId, useRef, useState } from "react";
import { Download, Maximize2, X } from "lucide-react";

export default function MermaidDiagram({ code }: { code: string }) {
  const reactId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const diagramId = `roomai-diagram-${reactId}`;
  const containerRef = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let canceled = false;
    setError("");
    import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
      return mermaid.render(diagramId, code.trim());
    }).then((result) => {
      if (!canceled) setSvg(result.svg);
    }).catch(() => {
      if (!canceled) setError("这段 Mermaid 暂时无法绘制，请查看源码。");
    });
    return () => { canceled = true; };
  }, [code, diagramId]);

  function downloadSvg() {
    const source = containerRef.current?.querySelector("svg")?.outerHTML;
    if (!source) return;
    const blob = new Blob([source], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "roomai-scheme.svg";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return <>
    <section className="mermaid-card" aria-label="方案图">
      <div className="mermaid-toolbar no-print"><span>方案图</span><button type="button" onClick={downloadSvg} title="导出 SVG"><Download size={13} /></button><button type="button" onClick={() => setExpanded(true)} title="放大查看"><Maximize2 size={13} /></button></div>
      {error ? <p className="mermaid-error">{error}</p> : <div className="mermaid-svg" ref={containerRef} dangerouslySetInnerHTML={{ __html: svg }} />}
    </section>
    {expanded && svg && <div className="mermaid-overlay no-print" onClick={() => setExpanded(false)}>
      <section className="mermaid-dialog" role="dialog" aria-modal="true" aria-label="放大方案图" onClick={(event) => event.stopPropagation()}>
        <div className="mermaid-toolbar"><span>方案图</span><button type="button" onClick={downloadSvg} title="导出 SVG"><Download size={14} /></button><button type="button" onClick={() => setExpanded(false)} title="关闭"><X size={16} /></button></div>
        <div className="mermaid-svg" dangerouslySetInnerHTML={{ __html: svg.replaceAll(diagramId, `${diagramId}-expanded`) }} />
      </section>
    </div>}
  </>;
}
