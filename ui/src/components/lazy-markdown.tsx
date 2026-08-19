// react-markdown + remark-gfm are only needed once a chat renders, so they
// load as a separate chunk. The fallback shows the raw text meanwhile.

import { lazy, Suspense } from "react";

const MarkdownInner = lazy(() => import("./markdown"));

export function Markdown({ text }: { text: string }) {
  return (
    <Suspense
      fallback={
        <div className="md-body text-sm break-words whitespace-pre-wrap">
          {text}
        </div>
      }
    >
      <MarkdownInner text={text} />
    </Suspense>
  );
}
