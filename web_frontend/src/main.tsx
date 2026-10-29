import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
async function start() {
 const token = new URLSearchParams(location.hash.slice(1)).get('session_token');
 if(token) {
  history.replaceState(null, '', location.pathname + location.search);
  const response = await fetch('/api/session', {method:'POST', headers:{'X-Session-Token':token}});
  if(!response.ok) throw new Error('Session initialization failed. Open the current startup link.');
 }
 ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
}
void start().catch(error => { document.getElementById('root')!.textContent = String(error); });
