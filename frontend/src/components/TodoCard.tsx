import { CalendarDays, Check, Circle, ListTodo, Minus, Pencil } from "lucide-react";
import { Todo } from "../lib/api";

type Props = {
  todos: Todo[];
  disabled: boolean;
  busyIds: number[];
  onToggle: (todo: Todo) => void;
  onEdit: (todo: Todo) => void;
};

export default function TodoCard({ todos, disabled, busyIds, onToggle, onEdit }: Props) {
  if (!todos.length) return null;
  return <section className="todo-card" aria-label="讨论待办卡片">
    <header className="todo-card-header">
      <span className="todo-card-icon"><ListTodo size={15} /></span>
      <span className="todo-card-heading"><b>讨论待办</b><small>本轮讨论提取 · {todos.length} 项</small></span>
    </header>
    <div className="todo-card-items">
      {todos.map((todo) => <div className={`todo-card-item ${todo.status !== "open" ? "todo-card-item-closed" : ""}`} key={todo.id}>
        <button type="button" className={`todo-card-check todo-card-check-${todo.status}`} disabled={disabled || busyIds.includes(todo.id)} onClick={() => onToggle(todo)} aria-label={todo.status === "done" ? `重新打开：${todo.title}` : `标记完成：${todo.title}`} title={todo.status === "done" ? "重新打开" : "标记完成"}>
          {todo.status === "done" ? <Check size={14} /> : todo.status === "dropped" ? <Minus size={14} /> : <Circle size={14} />}
        </button>
        <button type="button" className="todo-card-copy" disabled={disabled || busyIds.includes(todo.id)} onClick={() => onEdit(todo)}>
          <b>{todo.title}</b>
          <small>{todo.assignee || "待分配"}{todo.due_at ? ` · ${todo.due_at} 截止` : " · 未设截止日期"}</small>
        </button>
        <button type="button" className="todo-card-edit" disabled={disabled || busyIds.includes(todo.id)} onClick={() => onEdit(todo)} aria-label={`编辑待办：${todo.title}`} title="编辑待办"><Pencil size={14} /></button>
      </div>)}
    </div>
    <footer className="todo-card-footer"><CalendarDays size={12} />点击待办可修改负责人和截止日期</footer>
  </section>;
}
