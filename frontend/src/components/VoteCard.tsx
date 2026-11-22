import { Check, Clock3, LockKeyhole, Users } from "lucide-react";

export type VoteOption = { id: number; label: string; votes: number; percentage: number };
export type Vote = {
  id: number;
  session_id: string;
  message_id: number;
  question: string;
  status: "open" | "closed";
  created_at: string;
  closes_at: string | null;
  closed_at: string | null;
  total_votes: number;
  my_option_id: number | null;
  options: VoteOption[];
};

type Props = {
  vote: Vote;
  isOwner: boolean;
  busy: boolean;
  onVote: (voteId: number, optionId: number) => void;
  onClose: (voteId: number) => void;
};

export default function VoteCard({ vote, isOwner, busy, onVote, onClose }: Props) {
  const closed = vote.status !== "open";
  const closesAt = vote.closes_at ? new Date(vote.closes_at) : null;
  const remaining = closesAt && !closed ? Math.max(0, Math.ceil((closesAt.getTime() - Date.now()) / 60000)) : null;

  return <section className={`vote-card ${closed ? "vote-card-closed" : ""}`} aria-label="房间投票">
    <header className="vote-card-header">
      <span className="vote-card-icon"><Check size={15} /></span>
      <span className="vote-card-heading"><b>{closed ? "投票结果" : "团队投票"}</b><small>{closed ? "已结束" : remaining !== null ? `约 ${remaining} 分钟后结束` : "进行中"}</small></span>
      <span className="vote-count"><Users size={13} />{vote.total_votes}</span>
    </header>
    <h3>{vote.question}</h3>
    <div className="vote-options">
      {vote.options.map((option) => {
        const selected = vote.my_option_id === option.id;
        return <button className={`vote-option ${selected ? "vote-option-selected" : ""}`} key={option.id} disabled={closed || busy} onClick={() => onVote(vote.id, option.id)} aria-pressed={selected}>
          <span className="vote-option-radio">{selected ? <Check size={11} /> : null}</span>
          <span className="vote-option-label">{option.label}</span>
          {(closed || vote.total_votes > 0) && <span className="vote-option-score">{option.votes} · {option.percentage}%</span>}
          {(closed || vote.total_votes > 0) && <span className="vote-option-progress" style={{ width: `${option.percentage}%` }} />}
        </button>;
      })}
    </div>
    <footer className="vote-card-footer">
      <span><LockKeyhole size={12} /> 每人一票，可修改</span>
      {!closed && isOwner && <button disabled={busy} onClick={() => onClose(vote.id)}><Clock3 size={12} /> 结束投票</button>}
      {closed && <strong>{vote.total_votes ? `${vote.total_votes} 票` : "暂无投票"}</strong>}
    </footer>
  </section>;
}
