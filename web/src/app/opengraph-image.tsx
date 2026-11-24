import { ImageResponse } from "next/og";

export const alt = "E-Commerce Agents: six specialist agents on Microsoft Agent Framework";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
// Rendered once at build time, which a static export requires.
export const dynamic = "force-static";

const AGENTS = ["Product discovery", "Orders", "Pricing", "Reviews", "Inventory", "Support"];

/** The app's Open Graph image (#301). Blues and slate only; no external font, so the build needs no network. */
export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px",
          background: "linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%)",
          color: "#f8fafc",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 28, color: "#93c5fd", letterSpacing: 2 }}>OPEN SOURCE DEMO</div>
          <div style={{ fontSize: 84, fontWeight: 700, marginTop: 16 }}>E-Commerce Agents</div>
          <div style={{ fontSize: 36, color: "#cbd5e1", marginTop: 20 }}>
            Six specialist agents that talk over the A2A protocol, built on Microsoft Agent Framework.
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap" }}>
          {AGENTS.map((name) => (
            <div
              key={name}
              style={{
                fontSize: 26,
                marginRight: 16,
                marginBottom: 12,
                padding: "10px 22px",
                borderRadius: 999,
                border: "2px solid #60a5fa",
                color: "#dbeafe",
              }}
            >
              {name}
            </div>
          ))}
        </div>
      </div>
    ),
    size,
  );
}
