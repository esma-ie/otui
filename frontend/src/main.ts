import { mount } from "svelte";
import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/600.css";
import "@fontsource/dm-sans/700.css";
import "./app.css";
import App from "./App.svelte";

export default mount(App, { target: document.getElementById("app")! });
