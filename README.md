# VSTREAK
#### Description:

#### Project Overview & Design Philosophy

VSTREAK was born from the idea that traditional productivity tools often feel like a chore rather than an achievement. Most to-do lists are static and fail to provide the psychological "loop" required to build long-term habits. To solve this, I designed VSTREAK with a gamification-first philosophy, treating daily tasks as quests that contribute to a global character progression. The user interface was intentionally crafted to mirror a modern, premium SaaS startup environment—utilizing dark mode, glassmorphism, and smooth micro-interactions—to move away from the "industrial" feel of legacy productivity apps and toward a more engaging, high-performance workspace.

#### The Technical Architecture: Bridging Flask and SQLite

The backend architecture is built on a modular Flask framework, prioritizing clean separation of concerns and database integrity. Moving away from simplified educational libraries, I implemented a robust interaction layer using Python’s native sqlite3 module. This allowed for complex relational modeling where user authentication, daily task state, and historical streak data are tightly coupled. A critical component of the backend is the custom-built Gamification Engine, which programmatically calculates XP thresholds and progress percentages in real-time. This ensures that every database transaction (like marking a task as "complete") triggers a cascading logic update that modifies user levels and validates streak milestones without performance lag.

#### Context-Aware AI Integration with Gemini

What distinguishes VSTREAK from a standard CRUD application is the integration of the Google Gemini 2.5 Flash API. Rather than acting as a generic chatbot, the AI is "context-aware" through a process known as context injection. Every time a user interacts with the AI Coach, the backend runs a series of specialized SQL queries to gather the last seven days of productivity data, current XP, and level status. This data is then serialized and fed into the AI’s system instructions. This architectural choice allows the AI to provide hyper-personalized advice, such as identifying specific days when a user’s performance dipped or suggesting study schedules that account for tasks already present in the user's database, effectively acting as a data-driven personal mentor.

#### Data Visualization and Frontend Engineering

To provide users with actionable insights into their behavior, I integrated Chart.js and custom CSS logic to build a comprehensive analytics suite. The frontend features a dynamic 7-day performance graph that visualizes task completion trends, requiring seamless communication between the Python backend and JavaScript frontend via JSON endpoints. Furthermore, I implemented a 30-day productivity heatmap that provides a high-level overview of consistency. Each "cell" in the heatmap is interactive; clicking a date triggers an asynchronous fetch request to a specialized Flask route, which returns the specific completed and incomplete tasks for that day. This creates a highly responsive, single-page-application (SPA) feel that enhances the overall user experience.
