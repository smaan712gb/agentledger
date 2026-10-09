import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { Tooltip } from "./Tooltip";
import styles from "./Button.module.css";

export type ButtonTone = "primary" | "default" | "danger" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tone?: ButtonTone;
  size?: "sm" | "md";
  busy?: boolean;
  /**
   * Why the action is unavailable (the API's reason). The button stays visible, is disabled for the keyboard and
   * the pointer, and the reason is readable by both: as a tooltip and as the accessible description.
   */
  disabledReason?: string | undefined;
  children: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    tone = "default",
    size = "md",
    busy = false,
    disabledReason,
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  const isDisabled = Boolean(disabled) || busy || Boolean(disabledReason);
  const classes = [styles.button, styles[tone], size === "sm" ? styles.sm : "", className ?? ""].join(" ");
  const button = (
    <button
      ref={ref}
      type={type}
      className={classes}
      aria-disabled={isDisabled || undefined}
      disabled={disabled ?? busy}
      aria-busy={busy || undefined}
      {...rest}
      onClick={(e) => {
        if (isDisabled) {
          e.preventDefault();
          return;
        }
        rest.onClick?.(e);
      }}
    >
      {busy ? <span className={styles.spinner} aria-hidden="true" /> : null}
      <span>{children}</span>
    </button>
  );
  // aria-disabled (not the disabled attribute) keeps the button focusable so the reason can be read.
  return disabledReason ? <Tooltip content={disabledReason}>{button}</Tooltip> : button;
});
