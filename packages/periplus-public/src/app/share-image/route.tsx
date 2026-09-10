import { ImageResponse } from "next/og"

export const dynamic = "force-static"

export function GET() {
  return new ImageResponse(
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between", padding: 72, background: "#f7f9f5", color: "#18352c" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 38 }}>
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#18352c" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><path d="m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z" /></svg>
        <span>periplus</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        <div style={{ fontSize: 76, lineHeight: 1.05, maxWidth: 1000 }}>Research websites in one place.</div>
        <div style={{ fontSize: 28, color: "#4d665b" }}>Find mentions. Follow links. Compare sources.</div>
      </div>
      <div style={{ fontSize: 23 }}>Web research, built together</div>
    </div>,
    { width: 1200, height: 630 },
  )
}
