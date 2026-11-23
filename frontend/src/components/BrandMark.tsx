// RoomAI 品牌符号：气泡拼成的字母 R（AI 生成稿转透明 PNG，见 roomai-logo/logo-r-master.png）。
export default function BrandMark({ className = "" }: { className?: string }) {
  return <img src="/logo-r.png" alt="RoomAI" draggable={false} className={`brand-img${className ? ` ${className}` : ""}`} />;
}
