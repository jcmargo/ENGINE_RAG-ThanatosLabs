import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./routes/App.tsx";
import "./index.css";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("No se encontró el elemento raíz");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
