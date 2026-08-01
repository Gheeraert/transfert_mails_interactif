import torch

# Doit afficher True
print(torch.cuda.is_available())

# Doit afficher le nom de votre carte (ex: 'NVIDIA GeForce RTX 3060')
print(torch.cuda.get_device_name(0))