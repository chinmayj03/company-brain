/**
 * eval/fixtures/next-simple/app/layout.tsx
 * Root layout — fixture for framework-next extractor tests.
 */

export const metadata = {
  title: "Company Brain",
  description: "Knowledge graph for your codebase",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
