import { useEffect, useMemo, useRef, useState } from "react";

export interface CommandAction {
  id: string;
  label: string;
  run: () => void;
}

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  actions: CommandAction[];
}

/** ⌘K 命令面板：跳转与动作的快捷入口。 */
export function CommandPalette(props: CommandPaletteProps) {
  const { open, onClose, actions } = props;
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter((a) => a.label.toLowerCase().includes(q));
  }, [actions, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  if (!open) return null;

  return (
    <div
      className="palette-backdrop"
      role="presentation"
      onClick={onClose}
    >
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="palette-input"
          type="text"
          placeholder="输入命令…"
          aria-label="命令"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(filtered.length - 1, i + 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(0, i - 1));
            } else if (e.key === "Enter") {
              const hit = filtered[index];
              if (hit) {
                hit.run();
                onClose();
              }
            } else if (e.key === "Escape") {
              onClose();
            }
          }}
        />
        <ul className="palette-list">
          {filtered.map((a, i) => (
            <li key={a.id}>
              <button
                className={i === index ? "on" : ""}
                onMouseEnter={() => setIndex(i)}
                onClick={() => {
                  a.run();
                  onClose();
                }}
              >
                {a.label}
              </button>
            </li>
          ))}
          {filtered.length === 0 && <li className="muted">没有匹配的命令</li>}
        </ul>
      </div>
    </div>
  );
}
