import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

import styles from "./Field.module.css";

interface BaseProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | undefined;
}

export type InputFieldProps = BaseProps & Omit<InputHTMLAttributes<HTMLInputElement>, "id">;

/** A labelled input whose hint and error are announced with it (aria-describedby, aria-invalid). */
export const InputField = forwardRef<HTMLInputElement, InputFieldProps>(function InputField(
  { label, hint, error, className, ...input },
  ref,
) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className={[styles.field, className ?? ""].join(" ")}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <input
        id={id}
        ref={ref}
        className={styles.input}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(" ") || undefined}
        {...input}
      />
      {hint ? (
        <div id={hintId} className={styles.hint}>
          {hint}
        </div>
      ) : null}
      {error ? (
        <div id={errorId} className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
});

export type SelectFieldProps = BaseProps &
  Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> & { children: ReactNode };

export function SelectField({ label, hint, error, className, children, ...select }: SelectFieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  return (
    <div className={[styles.field, className ?? ""].join(" ")}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <select
        id={id}
        className={styles.input}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(" ") || undefined}
        {...select}
      >
        {children}
      </select>
      {hint ? (
        <div id={hintId} className={styles.hint}>
          {hint}
        </div>
      ) : null}
      {error ? (
        <div id={errorId} className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

export function CheckboxField({ label, hint, error, className, ...input }: InputFieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  return (
    <div className={[styles.field, styles.checkbox, className ?? ""].join(" ")}>
      <input id={id} type="checkbox" aria-describedby={hintId} {...input} />
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      {hint ? (
        <div id={hintId} className={styles.hint}>
          {hint}
        </div>
      ) : null}
      {error ? (
        <div className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
    </div>
  );
}

/** A summary of the problems a form has, focused when it appears, so keyboard and screen-reader users hear it. */
export function FormError({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <div className={styles.formError} role="alert">
      {message}
    </div>
  );
}
