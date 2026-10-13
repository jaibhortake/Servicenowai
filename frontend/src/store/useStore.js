import { create } from 'zustand'

export const useStore = create((set, get) => ({
  // Auth / user
  currentUser: null,
  currentUserEmail: '',
  setCurrentUser: (user) => set({ currentUser: user }),
  setCurrentUserEmail: (email) => set({ currentUserEmail: email }),

  // Active session
  sessionId: null,
  setSessionId: (id) => set({ sessionId: id }),

  // Interpret result
  interpretResult: null,
  setInterpretResult: (r) => set({ interpretResult: r }),

  // Selected catalog item
  selectedCatalog: null,
  setSelectedCatalog: (item) => set({ selectedCatalog: item }),

  // Tickets
  tickets: [],
  setTickets: (tickets) => set({ tickets }),

  // Last created ticket
  lastTicket: null,
  setLastTicket: (ticket) => set({ lastTicket: ticket }),

  // Chat messages (local mirror)
  chatMessages: [],
  addChatMessage: (msg) => set((s) => ({ chatMessages: [...s.chatMessages, msg] })),
  setChatMessages: (msgs) => set({ chatMessages: msgs }),
  clearChat: () => set({ chatMessages: [], interpretResult: null, selectedCatalog: null }),
}))