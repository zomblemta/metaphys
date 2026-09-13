import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "命理 · 知时",
  description: "从一份准确的命盘开始，展开关于自己的对话。",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
