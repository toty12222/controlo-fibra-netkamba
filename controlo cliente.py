import sqlite3
from datetime import datetime, timedelta
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk
import schedule
import time
import threading

class PaymentMonitor:
    def __init__(self, db_connection):
        self.conn = db_connection
        self.cursor = self.conn.cursor()
        self.setup_triggers()
        
    def setup_triggers(self):
        # Create a table for payment notifications
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_notifications (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            message TEXT,
            notification_date DATE,
            status TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        )''')

        # Create a table for service status
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS service_status (
            status_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            is_active BOOLEAN DEFAULT 1,
            last_status_change DATE,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        )''')
        
        # Create trigger for payment due date
        self.cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS payment_due_trigger
        AFTER INSERT ON payments
        BEGIN
            INSERT INTO payment_notifications (customer_id, message, notification_date, status)
            SELECT 
                NEW.customer_id,
                'Payment due on ' || NEW.due_date,
                date('now'),
                'PENDING'
            WHERE NEW.payment_made = 0;
        END;
        ''')
        
        self.conn.commit()

    def check_expired_payments(self):
        current_date = datetime.now().date()
        
        self.cursor.execute('''
        SELECT 
            c.customer_id,
            c.name,
            p.due_date,
            p.value,
            s.is_active
        FROM customers c
        JOIN payments p ON c.customer_id = p.customer_id
        LEFT JOIN service_status s ON c.customer_id = s.customer_id
        WHERE p.payment_made = 0 
        AND date(p.due_date) < date('now')
        AND (s.is_active = 1 OR s.is_active IS NULL)
        ''')
        
        expired_payments = self.cursor.fetchall()
        return expired_payments

    def calculate_next_due_date(self, current_date):
        # Calculate next due date (30 days from current date)
        next_due = current_date + timedelta(days=30)
        return next_due

    def toggle_service_status(self, customer_id, activate=True):
        try:
            self.cursor.execute('''
            INSERT OR REPLACE INTO service_status (customer_id, is_active, last_status_change)
            VALUES (?, ?, date('now'))
            ''', (customer_id, activate))
            
            status = "activated" if activate else "deactivated"
            self.cursor.execute('''
            INSERT INTO payment_notifications (customer_id, message, notification_date, status)
            VALUES (?, ?, date('now'), ?)
            ''', (customer_id, f"Service {status}", "INFO"))
            
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"Error toggling service status: {e}")
            return False

class ISPInterface(tk.Tk):
    def __init__(self):
        super().__init__()
        
        self.title("ISP Management System")
        self.conn = sqlite3.connect('isp_database.db')
        self.payment_monitor = PaymentMonitor(self.conn)
        
        self.setup_gui()
        self.start_monitoring()

    def setup_gui(self):
        # Expired Payments Frame
        expired_frame = ttk.LabelFrame(self, text="Expired Payments")
        expired_frame.grid(row=0, column=0, padx=10, pady=5, sticky="nsew")

        # Expired Payments Treeview
        self.expired_tree = ttk.Treeview(expired_frame, 
            columns=('ID', 'Name', 'Due Date', 'Amount', 'Status'),
            show='headings')
        
        for col in ('ID', 'Name', 'Due Date', 'Amount', 'Status'):
            self.expired_tree.heading(col, text=col)
            self.expired_tree.column(col, width=100)
            
        self.expired_tree.grid(row=0, column=0, padx=5, pady=5)

        # Control Buttons
        button_frame = ttk.Frame(expired_frame)
        button_frame.grid(row=1, column=0, pady=5)

        ttk.Button(button_frame, text="Refresh", 
                  command=self.refresh_expired_payments).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Toggle Service", 
                  command=self.toggle_selected_service).pack(side=tk.LEFT, padx=5)

        # Notifications Frame
        notif_frame = ttk.LabelFrame(self, text="Payment Notifications")
        notif_frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")

        self.notif_text = tk.Text(notif_frame, height=5, width=50)
        self.notif_text.grid(row=0, column=0, padx=5, pady=5)

    def refresh_expired_payments(self):
        # Clear existing items
        for item in self.expired_tree.get_children():
            self.expired_tree.delete(item)

        # Get expired payments
        expired = self.payment_monitor.check_expired_payments()
        
        for payment in expired:
            status = "Active" if payment[4] else "Inactive"
            self.expired_tree.insert('', 'end', values=(
                payment[0],  # Customer ID
                payment[1],  # Name
                payment[2],  # Due Date
                f"${payment[3]:.2f}",  # Amount
                status
            ))

    def toggle_selected_service(self):
        selected = self.expired_tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Please select a customer")
            return

        item = self.expired_tree.item(selected[0])
        customer_id = item['values'][0]
        current_status = item['values'][4]
        
        # Toggle status
        new_status = current_status != "Active"
        if self.payment_monitor.toggle_service_status(customer_id, new_status):
            self.refresh_expired_payments()
            status = "activated" if new_status else "deactivated"
            messagebox.showinfo("Success", f"Service successfully {status}")
        else:
            messagebox.showerror("Error", "Failed to toggle service status")

    def check_payments(self):
        expired = self.payment_monitor.check_expired_payments()
        if expired:
            self.notif_text.delete(1.0, tk.END)
            self.notif_text.insert(tk.END, "Overdue Payments:\n")
            for payment in expired:
                self.notif_text.insert(tk.END, 
                    f"Customer: {payment[1]} - Due Date: {payment[2]} - Amount: ${payment[3]:.2f}\n")
        self.refresh_expired_payments()

    def start_monitoring(self):
        def run_schedule():
            while True:
                schedule.run_pending()
                time.sleep(1)

        schedule.every(1).minutes.do(self.check_payments)
        threading.Thread(target=run_schedule, daemon=True).start()

    def run(self):
        self.check_payments()  # Initial check
        self.mainloop()

if __name__ == "__main__":
    app = ISPInterface()
    app.run()
