import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type Row,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, useState, type ReactNode } from "react";

import styles from "./DataTable.module.css";

export interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  /** A short description of the table for assistive technology. */
  caption: string;
  getRowId?: (row: T) => string;
  /** Rows beyond this count are virtualised (only the visible window is rendered). */
  virtualiseAbove?: number;
  rowHeight?: number;
  renderEmpty?: () => ReactNode;
  initialSorting?: SortingState;
}

/** A sortable grid on TanStack Table; long lists virtualise with TanStack Virtual. Sorting is keyboard operable. */
export function DataTable<T>({
  data,
  columns,
  caption,
  getRowId,
  virtualiseAbove = 60,
  rowHeight = 44,
  renderEmpty,
  initialSorting = [],
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    ...(getRowId ? { getRowId } : {}),
  });
  const rows = table.getRowModel().rows;
  const virtual = rows.length > virtualiseAbove;
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: virtual ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  });

  const header = (
    <thead>
      {table.getHeaderGroups().map((hg) => (
        <tr key={hg.id}>
          {hg.headers.map((h) => {
            const sorted = h.column.getIsSorted();
            const canSort = h.column.getCanSort();
            return (
              <th
                key={h.id}
                scope="col"
                aria-sort={
                  sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : canSort ? "none" : undefined
                }
                className={h.column.columnDef.meta?.align === "right" ? "num" : undefined}
                style={h.getSize() !== 150 ? { width: h.getSize() } : undefined}
              >
                {h.isPlaceholder ? null : canSort ? (
                  <button type="button" className={styles.sortButton} onClick={h.column.getToggleSortingHandler()}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    <span aria-hidden="true" className={styles.sortMark}>
                      {sorted === "asc" ? "▲" : sorted === "desc" ? "▼" : ""}
                    </span>
                  </button>
                ) : (
                  flexRender(h.column.columnDef.header, h.getContext())
                )}
              </th>
            );
          })}
        </tr>
      ))}
    </thead>
  );

  const renderRow = (row: Row<T>, style?: React.CSSProperties) => (
    <tr key={row.id} style={style}>
      {row.getVisibleCells().map((cell) => (
        <td key={cell.id} className={cell.column.columnDef.meta?.align === "right" ? "num" : undefined}>
          {flexRender(cell.column.columnDef.cell, cell.getContext())}
        </td>
      ))}
    </tr>
  );

  if (!rows.length && renderEmpty) {
    return <div>{renderEmpty()}</div>;
  }

  if (!virtual) {
    return (
      <div className={styles.wrap}>
        <table className={styles.table}>
          <caption className="visually-hidden">{caption}</caption>
          {header}
          <tbody>{rows.map((r) => renderRow(r))}</tbody>
        </table>
      </div>
    );
  }

  const items = virtualizer.getVirtualItems();
  const first = items[0];
  const last = items[items.length - 1];
  const padTop = first ? first.start : 0;
  const padBottom = last ? virtualizer.getTotalSize() - last.end : 0;
  return (
    <div className={[styles.wrap, styles.scroll].join(" ")} ref={scrollRef}>
      <table className={styles.table}>
        <caption className="visually-hidden">
          {caption} ({rows.length} rows)
        </caption>
        {header}
        <tbody>
          {padTop > 0 ? (
            <tr aria-hidden="true">
              <td style={{ height: padTop, padding: 0, border: 0 }} colSpan={columns.length} />
            </tr>
          ) : null}
          {items.map((item) => {
            const row = rows[item.index];
            return row ? renderRow(row, { height: item.size }) : null;
          })}
          {padBottom > 0 ? (
            <tr aria-hidden="true">
              <td style={{ height: padBottom, padding: 0, border: 0 }} colSpan={columns.length} />
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

declare module "@tanstack/react-table" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData, TValue> {
    align?: "left" | "right";
  }
}
