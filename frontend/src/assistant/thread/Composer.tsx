import { ComposerPrimitive, ThreadPrimitive } from "@assistant-ui/react";
import { ArrowUp, Square } from "lucide-react";
import type { ReactNode } from "react";

type ComposerProps = {
  /** 左侧插槽（如 ApprovalPicker / 模型选择） */
  left?: ReactNode;
  /** 右侧插槽（如按钮前的附加操作） */
  right?: ReactNode;
  placeholder?: string;
};

/**
 * 底部输入区：Enter 发送、Shift+Enter 换行（primitive 内建），
 * running 时切换为“停止”按钮；isDisabled 时整体禁用（pendingJob 场景由 provider 注入）。
 */
export function Composer({ left, right, placeholder = "输入问题，Enter 发送" }: ComposerProps) {
  return (
    <ComposerPrimitive.Root className="aui-composer">
      <div className="aui-composer-input">
        <ComposerPrimitive.Input
          rows={1}
          autoFocus
          placeholder={placeholder}
          className="aui-composer-textarea"
        />
      </div>
      <div className="aui-composer-bar">
        <div className="flex min-w-0 items-center gap-2">{left}</div>
        <div className="flex items-center gap-2">
          {right}
          <ThreadPrimitive.If running={false}>
            <ComposerPrimitive.Send asChild>
              <button type="button" className="aui-send-btn" aria-label="发送">
                <ArrowUp size={16} />
              </button>
            </ComposerPrimitive.Send>
          </ThreadPrimitive.If>
          <ThreadPrimitive.If running>
            <ComposerPrimitive.Cancel asChild>
              <button type="button" className="aui-send-btn aui-cancel-btn" aria-label="停止">
                <Square size={12} fill="currentColor" />
              </button>
            </ComposerPrimitive.Cancel>
          </ThreadPrimitive.If>
        </div>
      </div>
    </ComposerPrimitive.Root>
  );
}
