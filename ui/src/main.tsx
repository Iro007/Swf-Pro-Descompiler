import {createRoot} from 'react-dom/client';
import App from './App';
import './index.css';
import './shell/shell.css';
import {applyTheme,storedTheme} from './shell/util';

// Resolve light/dark before the first paint so the window never flashes the wrong theme.
applyTheme(storedTheme());
createRoot(document.getElementById('root')!).render(<App/>);
