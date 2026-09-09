import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "긴급 구조 작전 | Microsoft Foundry",
  description: "세 사람의 구조 순서를 정하고 자동 드론 탐지를 체험하는 가상 긴급 구조 훈련.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="h-full flex flex-col">{children}</body>
    </html>
  );
}
