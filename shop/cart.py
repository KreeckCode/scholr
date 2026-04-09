"""
Shopping cart session handler

Security:
- Server-side validation
- Price verification from database
- Session-based (not cookies to prevent tampering)
"""

from decimal import Decimal
from accounts.models import Product


class Cart:
    """
    Shopping cart stored in Django session
    
    Security:
    - Stores only product IDs and quantities (not prices)
    - Prices always fetched from database
    - Session-based storage
    """
    
    def __init__(self, request):
        """Initialize cart from session"""
        self.session = request.session
        self.request = request
        cart = self.session.get('cart')
        
        if not cart:
            cart = self.session['cart'] = {}
        
        self.cart = cart
    
    def add(self, product, quantity=1):
        """
        Add product to cart
        
        Security: Validates product exists and is active
        """
        # Validate product
        if isinstance(product, int):
            product = Product.objects.get(id=product, is_active=True)
        
        product_id = str(product.id)
        
        if product_id not in self.cart:
            self.cart[product_id] = {'quantity': 0}
        
        self.cart[product_id]['quantity'] += quantity
        self.save()
    
    def update_quantity(self, product, quantity):
        """Update product quantity"""
        product_id = str(product.id if isinstance(product, Product) else product)
        
        if product_id in self.cart:
            if quantity > 0:
                self.cart[product_id]['quantity'] = quantity
            else:
                del self.cart[product_id]
            self.save()
    
    def remove(self, product):
        """Remove product from cart"""
        product_id = str(product.id if isinstance(product, Product) else product)
        
        if product_id in self.cart:
            del self.cart[product_id]
            self.save()
    
    def save(self):
        """Mark session as modified"""
        self.session.modified = True
    
    def clear(self):
        """Clear cart"""
        if 'cart' in self.session:
            del self.session['cart']
            self.save()
    
    def __iter__(self):
        """
        Iterate over cart items with database products
        
        Security: Always fetches fresh prices from database
        """
        product_ids = self.cart.keys()
        products = Product.objects.filter(
            id__in=product_ids,
            is_active=True
        )
        
        cart = self.cart.copy()
        
        for product in products:
            item = cart[str(product.id)]
            item['product'] = product
            item['price'] = product.price  # From DB, not cart
            item['total_price'] = product.price * item['quantity']
            yield item
    
    def __len__(self):
        """Count total items"""
        return sum(item['quantity'] for item in self.cart.values())
    
    def get_total_price(self):
        """
        Calculate total
        
        Security: Uses DB prices, not cart prices
        """
        total = Decimal('0.00')
        for item in self:
            total += item['total_price']
        return total
    
    def has_free_items(self):
        """Check if cart has free products"""
        for item in self:
            if item['product'].is_free:
                return True
        return False
    
    def has_paid_items(self):
        """Check if cart has paid products"""
        for item in self:
            if not item['product'].is_free:
                return True
        return False
    
    def requires_payment(self):
        """Check if payment required"""
        return self.get_total_price() > 0
    
    def get_products(self):
        """Get list of Product instances in cart"""
        product_ids = self.cart.keys()
        return Product.objects.filter(id__in=product_ids, is_active=True)