// 每位成员的固定配色：优先按成员在房间里的顺序取色（同房间内不重复），
// 不在成员列表里时退回按 key 哈希取色。所有人看到的同一人都是同一种颜色。
export type MemberColor = { background: string; color: string };

export const MEMBER_PALETTE: MemberColor[] = [
  { background: "#e3ecfd", color: "#2f5fd0" },
  { background: "#e2f4e6", color: "#1e7a3c" },
  { background: "#fdeeda", color: "#b25e09" },
  { background: "#efe7fd", color: "#6b46c1" },
  { background: "#fde7ef", color: "#b8326b" },
  { background: "#ddf3f3", color: "#0f766e" },
  { background: "#fdf3d3", color: "#92600a" },
  { background: "#fde4e2", color: "#b3261e" },
  { background: "#f9e2f7", color: "#a21caf" },
  { background: "#e0f2fe", color: "#0369a1" },
];

export function memberColorAt(index: number): MemberColor {
  const len = MEMBER_PALETTE.length;
  return MEMBER_PALETTE[((index % len) + len) % len];
}

export function memberColor(key: string): MemberColor {
  let hash = 5381;
  for (let i = 0; i < key.length; i++) hash = ((hash * 33) ^ key.charCodeAt(i)) >>> 0;
  return MEMBER_PALETTE[hash % MEMBER_PALETTE.length];
}
