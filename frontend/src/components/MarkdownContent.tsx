import { Children, isValidElement } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import MermaidDiagram from "./MermaidDiagram";

export default function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
          table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div>,
          code: ({ className, children, ...props }) => className?.includes("language-mermaid")
            ? <MermaidDiagram code={String(children).replace(/\n$/, "")} />
            : <code className={className} {...props}>{children}</code>,
          pre: ({ children }) => {
            const child = Children.toArray(children)[0];
            if (isValidElement(child) && child.type === MermaidDiagram) return child;
            return <pre className="markdown-pre">{children}</pre>;
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
