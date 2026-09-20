import type { Metadata } from "next";
import UIScale from "@/components/UIScale";
import "./globals.css";

export const metadata: Metadata = {
  title: "코드 레드: 드론 신고 정찰대 | Microsoft Foundry",
  description: "세 사람의 신고 순서를 정하고 자동 드론 탐지·119 신고를 체험하는 가상 긴급 훈련.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="h-full flex flex-col">
        <UIScale />
        {children}
      </body>
    </html>
  );
}
