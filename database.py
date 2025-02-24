import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import os

class DatabaseManager:
    def __init__(self, db_name='isp_database.db'):
        # Ensure data directory exists
        self.data_dir = 'data'
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        # Full path to database
        self.db_path = os.path.join(self.data_dir, db_name)
        self.conn = sqlite3.connect(self.db_path)
        
        # Enable foreign keys and optimize for better performance
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")  # Write-Ahead Logging
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA cache_size = -2000000")  # Use 2GB of cache
        self.conn.execute("PRAGMA temp_store = MEMORY")
        
        self.cursor = self.conn.cursor()
        self.create_tables()
        self.create_indexes()
        self.setup_triggers()

    def create_tables(self):
        # Create tables with optimized data types and indexes
        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS customers (
            customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE,
            address TEXT,
            phone TEXT,
            mbps INTEGER,
            state TEXT,
            contract_date DATE,
            payment_day INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS payment_methods (
            payment_method_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            payment_type TEXT,
            bank TEXT,
            iban TEXT,
            expiration_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        )''')

        self.cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER,
            payment_date DATE,
            due_date DATE,
            value DECIMAL(10,2),
            payment_made BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        )''')

        self.conn.commit()

    def create_indexes(self):
        # Create indexes for better query performance
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_customer_name ON customers(name)",
            "CREATE INDEX IF NOT EXISTS idx_customer_state ON customers(state)",
            "CREATE INDEX IF NOT EXISTS idx_payment_date ON payments(due_date)",
            "CREATE INDEX IF NOT EXISTS idx_payment_customer ON payments(customer_id)",
            "CREATE INDEX IF NOT EXISTS idx_payment_status ON payments(payment_made)",
        ]
        
        for index in indexes:
            self.cursor.execute(index)
        
        self.conn.commit()

    def setup_triggers(self):
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

    def register_customer(self, customer_data, payment_data):
        try:
            # Insert customer
            self.cursor.execute('''
            INSERT INTO customers (
                name, address, phone, mbps, state, 
                contract_date, payment_day
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                customer_data['name'],
                customer_data['address'],
                customer_data['phone'],
                customer_data['mbps'],
                customer_data['state'],
                customer_data['contract_date'],
                customer_data['payment_day']
            ))
            
            customer_id = self.cursor.lastrowid
            
            # Insert payment method
            self.cursor.execute('''
            INSERT INTO payment_methods (
                customer_id, payment_type, bank, 
                iban, expiration_date
            )
            VALUES (?, ?, ?, ?, ?)
            ''', (
                customer_id,
                payment_data['payment_type'],
                payment_data['bank'],
                payment_data['iban'],
                payment_data['expiration_date']
            ))

            # Calculate next payment date
            current_date = datetime.now()
            due_date = datetime.strptime(customer_data['contract_date'], '%Y-%m-%d')
            
            # Set the payment day
            due_date = due_date.replace(day=int(customer_data['payment_day']))
            
            # If the payment day has passed this month, move to next month
            if due_date.day < current_date.day:
                # Add one month
                if due_date.month == 12:
                    due_date = due_date.replace(year=due_date.year + 1, month=1)
                else:
                    due_date = due_date.replace(month=due_date.month + 1)

            self.cursor.execute('''
            INSERT INTO payments (
                customer_id, payment_date, due_date, 
                value, payment_made
            )
            VALUES (?, NULL, ?, ?, 0)
            ''', (
                customer_id,
                due_date.strftime('%Y-%m-%d'),
                payment_data['value']
            ))
            
            self.conn.commit()
            return True, customer_id
        except sqlite3.Error as e:
            self.conn.rollback()
            return False, str(e)

    def get_all_customers(self, page=1, per_page=100, filters=None):
        try:
            query = '''
            SELECT 
                c.customer_id,
                c.name,
                c.address,
                c.phone,
                c.mbps,
                c.state,
                c.contract_date,
                c.payment_day,
                pm.payment_type,
                pm.bank,
                p.value as monthly_value,
                p.payment_date as last_payment_date,
                CASE
                    WHEN p.payment_made = 1 THEN 'Paid'
                    WHEN date(p.due_date) < date('now') THEN 'Overdue'
                    ELSE 'Pending'
                END as payment_status
            FROM customers c
            LEFT JOIN payment_methods pm ON c.customer_id = pm.customer_id
            LEFT JOIN payments p ON c.customer_id = p.customer_id
            '''
            
            params = []
            if filters:
                conditions = []
                if filters.get('name'):
                    conditions.append("c.name LIKE ?")
                    params.append(f"%{filters['name']}%")
                if filters.get('state'):
                    conditions.append("c.state = ?")
                    params.append(filters['state'])
                if filters.get('payment_status'):
                    if filters['payment_status'] == 'Paid':
                        conditions.append("p.payment_made = 1")
                    elif filters['payment_status'] == 'Overdue':
                        conditions.append("p.payment_made = 0 AND date(p.due_date) < date('now')")
                    elif filters['payment_status'] == 'Pending':
                        conditions.append("p.payment_made = 0 AND date(p.due_date) >= date('now')")
                
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY c.name LIMIT ? OFFSET ?"
            params.extend([per_page, (page - 1) * per_page])
            
            self.cursor.execute(query, params)
            return self.cursor.fetchall()
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return None

    def get_total_customers(self, filters=None):
        try:
            query = "SELECT COUNT(*) FROM customers c"
            params = []
            
            if filters:
                conditions = []
                if filters.get('name'):
                    conditions.append("c.name LIKE ?")
                    params.append(f"%{filters['name']}%")
                if filters.get('state'):
                    conditions.append("c.state = ?")
                    params.append(filters['state'])
                
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
            
            self.cursor.execute(query, params)
            return self.cursor.fetchone()[0]
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return 0

    def update_customer_status(self, customer_id, new_status):
        try:
            self.cursor.execute('''
            UPDATE customers 
            SET state = ? 
            WHERE customer_id = ?
            ''', (new_status, customer_id))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def record_payment(self, customer_id):
        try:
            current_date = datetime.now().strftime('%Y-%m-%d')
            self.cursor.execute('''
            UPDATE payments 
            SET payment_made = 1,
                payment_date = ?
            WHERE customer_id = ? 
            AND payment_made = 0
            ''', (current_date, customer_id))
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def close(self):
        self.conn.close()

    def get_monthly_payments(self, month, year):
        try:
            self.cursor.execute('''
            SELECT 
                c.customer_id,
                c.name,
                c.phone,
                p.due_date,
                p.value,
                p.payment_made,
                pm.payment_type,
                pm.bank,
                c.payment_day
            FROM customers c
            JOIN payments p ON c.customer_id = p.customer_id
            LEFT JOIN payment_methods pm ON c.customer_id = pm.customer_id
            WHERE strftime('%m', p.due_date) = ? 
            AND strftime('%Y', p.due_date) = ?
            ORDER BY p.due_date
            ''', (f"{month:02d}", str(year)))
            
            return self.cursor.fetchall()
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return None

    def import_excel_data(self, file_path, sheet_name=None):
        try:
            # Read Excel file
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            
            # Begin transaction
            self.conn.execute("BEGIN TRANSACTION")
            
            # Process each row
            success_count = 0
            error_count = 0
            errors = []
            
            for index, row in df.iterrows():
                try:
                    customer_data = {
                        'name': row['name'],
                        'address': row['address'],
                        'phone': str(row['phone']),
                        'mbps': int(row['mbps']),
                        'state': row['state'],
                        'contract_date': row['contract_date'].strftime('%Y-%m-%d'),
                        'payment_day': int(row['payment_day'])
                    }
                    
                    payment_data = {
                        'payment_type': row['payment_type'],
                        'bank': row['bank'],
                        'iban': row['iban'],
                        'value': float(row['monthly_value']),
                        'expiration_date': row['expiration_date'].strftime('%Y-%m-%d')
                    }
                    
                    success, result = self.register_customer(customer_data, payment_data)
                    if success:
                        success_count += 1
                    else:
                        error_count += 1
                        errors.append(f"Row {index + 2}: {result}")
                
                except Exception as e:
                    error_count += 1
                    errors.append(f"Row {index + 2}: {str(e)}")
            
            # Commit transaction if there were no errors
            if error_count == 0:
                self.conn.commit()
                return True, f"Successfully imported {success_count} customers"
            else:
                self.conn.rollback()
                error_message = f"Imported {success_count} customers with {error_count} errors:\n"
                error_message += "\n".join(errors[:10])
                if len(errors) > 10:
                    error_message += f"\n... and {len(errors) - 10} more errors"
                return False, error_message
                
        except Exception as e:
            self.conn.rollback()
            return False, f"Error importing data: {str(e)}"

    def backup_database(self):
        try:
            backup_path = os.path.join(self.data_dir, 
                f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db')
            
            backup = sqlite3.connect(backup_path)
            self.conn.backup(backup)
            backup.close()
            return True, backup_path
        except sqlite3.Error as e:
            return False, str(e)
